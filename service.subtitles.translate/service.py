# ----------------------------------------------------------------------------------------------------------------------
#  Copyright (c) 2023.
#  This file is part of translate_subs
#  SPDX-License-Identifier: GPL-2.0-or-later
#  See LICENSE.txt
# ----------------------------------------------------------------------------------------------------------------------
import os.path
import threading
import enum
import time

import xbmc
import xbmcgui
from xbmc import Player, Monitor
from resources.lib.translatepy import Language

from resources.lib import addon_log
from resources.lib import utils
from resources.lib import kodi_utils
from resources.lib.subtitles import translate


logger = addon_log.logging.getLogger('.'.join((utils.logger_id, __name__.split('.', 2)[-1])))

INDENT = ' ' * 60

class TranslateOption(enum.IntEnum):
    Extract=0
    UseLocal=1
    Cancel=2


def prompt_user_translate_subtitles()->TranslateOption:
    dialog = xbmcgui.Dialog()
    # options = TranslateOption._member_names_
    # default_option = TranslateOption.UseLocal

    # selected = dialog.select("Translate subtitles?",options, preselect=default_option)
    selected = dialog.yesnocustom(
        "Translate subtitles?",
        "Currently subtitles not the default language.\n"
        "'Try to extract': extract 'eng' subs from the video file first (take long time)\n"
        "'Use current': use the currently loaded subs for translation",
        customlabel="Try to extract",
        nolabel="Cancel",
        yeslabel="Use current",
        defaultbutton=xbmcgui.DLG_YESNO_YES_BTN
    )

    if selected <= 0:
        selected_option = TranslateOption.Cancel
    elif selected == 1:
        selected_option = TranslateOption.UseLocal
    else:
        selected_option = TranslateOption.Extract
    return selected_option


class PlayerMonitor(Player):
    def __init__(self):
        super(PlayerMonitor, self).__init__()
        self.monitor = Monitor()
        self._cur_file = None
        self._subtitles_may_be_downloaded = False

    def onAVStarted(self) -> None:
        utils.addon_info.initialise()
        if not utils.addon_info.addon.getSettingBool('run_on_start'):
            logger.debug("Automatic translation disabled in settings.")
            return
        self._execute_subtitles_translation()

    def onPlayBackPaused(self) -> None:
        utils.addon_info.initialise()
        if utils.addon_info.addon.getSettingBool('run_on_pause'):
            if xbmc.getCondVisibility('Window.IsActive(subtitlesearch)'):
                logger.debug("Paused for subtitles selection. Skipping execution as most likely correct subtitles will be downloaded")
                self._subtitles_may_be_downloaded = True
            else:
                self._execute_subtitles_translation()
    
    def onPlayBackResumed(self) -> None:
        utils.addon_info.initialise()
        if utils.addon_info.addon.getSettingBool('run_on_pause') and self._subtitles_may_be_downloaded:
            self._subtitles_may_be_downloaded = False
            self._execute_subtitles_translation()

    def _execute_subtitles_translation(self) -> None:
        # noinspection PyBroadException
        logger.debug("_execute_subtitles_translation, playing file\n"
                     "%s file: %s\n"
                     "%s video streams: %s\n"
                     "%s audio streams: %s\n"
                     "%s subtitle streams: %s\n"
                     "%s subtitles: %s",
                     INDENT, self.getPlayingFile(),
                     INDENT, self.getAvailableVideoStreams(),
                     INDENT, self.getAvailableAudioStreams(),
                     INDENT, self.getAvailableSubtitleStreams(),
                     INDENT, self.getSubtitles())
            
        preferred_lang = Language(kodi_utils.get_preferred_subtitle_lang()).id

        if self.getSubtitles() != '' and preferred_lang != self.getSubtitles():
            logger.debug(f"Language of active subtitles {self.getSubtitles()} differs from {preferred_lang=}. Asking user input")
            user_selection_trans = prompt_user_translate_subtitles()
            logger.debug(user_selection_trans)
            if user_selection_trans == TranslateOption.Cancel:
                logger.debug("User does not want to translate")
                return
            logger.debug("User wants translation")
        else:
            logger.debug("No subtitle loaded or already in expected language")
            return

        li = self.getPlayingItem()
        file_name = li.getProperty('subtitles.translate.file')
        if not file_name:
            movie_file_name = os.path.splitext(self.getPlayingFile())[0]
            # ideally we should be able to detect which exact subtitle is being used and extract if embedded one used used,
            # however I couldn't figure out an easy way, so for now this will do it; the user must tell explicitly.
            if user_selection_trans == TranslateOption.Extract:
                logger.debug(f"Trying to extract from video file.")
                # first try to use the embedded subtitle in english, must be the best, but will only work if ffmpeg is installed.
                file_name = f"{movie_file_name}.en.srt"
                if not utils.extract_subtitles(self.getPlayingFile(), file_name, "eng"):
                    logger.debug("Couldn't extract the subtitle from movie.")
                # make a small sleep as it seems that in RPi2 (at least) after file extraction it will appear empty for a while
                time.sleep(2)
            # we will always try local subs in the wors case
            if not file_name or not os.path.exists(file_name):
                file_name = f"{movie_file_name}.{Language(self.getSubtitles()).alpha2}.srt"
                logger.debug(f"Trying to find local subtitles {file_name=}")
                if not os.path.exists(file_name):
                    logger.debug(f"Could not find a suitable subtitle for translation. Trying to find '{file_name=}' in the movie's directory")
                    return

        base_name, file_extension = os.path.splitext(file_name)
        subs_type = li.getProperty('subtitles.translate.type')
        orig_lang = li.getProperty('subtitles.translate.orig_lang')
        filter_flags = li.getProperty('subtitles.translate.filter_flags')

        logger.info("Property file: '%s'", file_name)
        logger.info("Property type: '%s'", subs_type)
        logger.info("Property original language: '%s'", orig_lang)
        logger.info("Property filter_flags: '%s'", filter_flags)

        if not subs_type:
            subs_type = file_extension
        if subs_type not in translate.supported_types:
            logger.info("Unsupported subtitle type '%s'", subs_type)
            return

        # Get the original langauge by property, or by a langauge id in the filename. Default to 'auto'
        if not orig_lang:
            orig_lang = self.getSubtitles()
            logger.debug(f"Original language not detected. Assuming {orig_lang}")

        try:
            filter_flags = int(filter_flags)
        except ValueError:
            filter_flags = -1

        # Strip the querystring from the video url, because it may contain items unique to every instance played.
        video_file = self.getPlayingFile().split('?')[0]
        preferred_display_time = utils.addon_info.addon.getSettingNumber('display_time')

        logger.info("Subtitles file: '%s'", file_name)
        logger.info("Subtitles type: '%s'", subs_type)
        logger.info("Subtitles original language: '%s'", orig_lang)
        logger.info("Subtitles filter_flags: '%s'", filter_flags)
        logger.info("Video ID: %s", video_file)
        logger.info("Display time: %s", preferred_display_time)

        xbmcgui.Dialog().notification("Auto Translate Subtitles", f"Translation starting {orig_lang}", xbmcgui.NOTIFICATION_INFO, 5000)
        translated_fname = translate.translate_file(video_file, file_name, subs_type,
                                                    src_lang=orig_lang, filter_flags=filter_flags,
                                                    display_time=preferred_display_time)
        if not translated_fname:
            logger.debug("No translated file name. Exit")
            xbmcgui.Dialog().notification("Auto Translate Subtitles", "Translation failed.", xbmcgui.NOTIFICATION_ERROR, 5000)
            return
        else:
            logger.debug(f"Loading file {translated_fname=}")
            xbmcgui.Dialog().notification("Auto Translate Subtitles", f"Loading file {translated_fname=}", xbmcgui.NOTIFICATION_INFO, 5000)
        # Translating can take some time, check if the file is still playing
        if video_file not in self.getPlayingFile():
            logger.info("Abort. It looks like another file has been started while translation was in progress.")
            return
        # it is also possible that other subtitle of the desired target language as downloaded meanwhile
        if preferred_lang == self.getSubtitles():
            logger.info("Abort. Subtitles of the desired language are already active. It is possible that other subtitle of the desired target language as downloaded meanwhile")
            return
        
        logger.debug("Using translated subtitles: '%s'", translated_fname)
        self.setSubtitles(translated_fname)


if __name__ == '__main__':
    logger.debug("Running translate service from thead %s", threading.current_thread().native_id)
    translate.cleanup_cached_files()
    system_monitor = xbmc.Monitor()
    while system_monitor.abortRequested() is False:
        try:
            player = PlayerMonitor()
            while system_monitor.abortRequested() is False:
                system_monitor.waitForAbort(86400)
                logger.info("Abort requested")
                translate.cleanup_cached_files()
        except Exception as e:
            logger.error("Unhandled exception: %r:", e, exc_info=True)
    logger.info("Ended service")
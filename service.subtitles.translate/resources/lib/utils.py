# ----------------------------------------------------------------------------------------------------------------------
#  Copyright (c) 2022-2023.
#  This file is part of translate_subs
#  SPDX-License-Identifier: GPL-2.0-or-later
#  See LICENSE.txt
# ----------------------------------------------------------------------------------------------------------------------

from __future__ import annotations

import json
import logging
import os
import subprocess

import xbmc
import xbmcgui
from xbmcvfs import translatePath
import xbmcaddon

from resources.lib.translatepy import Language

__version__ = "0.2.0"


class AddonInfo:
    def __init__(self):
        self.initialise()
        self.name = self.addon.getAddonInfo("name")
        self.id = self.addon.getAddonInfo("id")
        self.profile = translatePath(self.addon.getAddonInfo('profile'))
        self.addon_dir = os.path.join(translatePath('special://home'), self.id)
        self.temp_dir = os.path.join(translatePath('special://temp'), 'translated_subs')
        os.makedirs(self.temp_dir, exist_ok=True)

    # noinspection PyAttributeOutsideInit
    def initialise(self):
        self.addon = addon = xbmcaddon.Addon()
        self.localise = addon.getLocalizedString


addon_info = AddonInfo()
logger_id = addon_info.name.replace(' ', '-').replace('.', '-')
logger = logging.getLogger(logger_id + '.utils')


def get_os():
    import platform
    return platform.system(), platform.machine()


def log(message, *args, **kwargs):
    xbmc.log('[subtitles.translate] ' + message.format(*args, **kwargs), xbmc.LOGDEBUG)


def mark_error():
    from datetime import datetime
    import shutil

    player = xbmc.Player()
    try:
        playing_file = player.getPlayingFile()
        play_time = player.getTime()
        orig_subs = player.getPlayingItem().getProperty('subtitles.translate.file')
    except (RuntimeError, OSError):
        return

    if not orig_subs or not playing_file:
        return

    try:
        with open(os.path.join(addon_info.profile, 'subtitles', 'last_translation'), 'r') as f:
            translated_subs = f.read().strip()
    except FileNotFoundError:
        translated_subs = ''

    current_dt = datetime.now().strftime('%Y-%m-%d %H-%M-%S')
    error_dir = os.path.join(addon_info.profile, 'errors', current_dt)
    os.makedirs(error_dir, exist_ok=True)

    shutil.copy(orig_subs, error_dir)
    # shutil.copy(translated_subs, error_dir)
    try:
        shutil.copy(os.path.join(addon_info.profile, 'subtitles', 'orig.txt'), error_dir)
        shutil.copy(os.path.join(addon_info.profile, 'subtitles', 'srt_filtered.txt'), error_dir)
        shutil.copy(os.path.join(addon_info.profile, 'subtitles', 'translated.txt'), error_dir)
        source = 'web'
    except FileNotFoundError:
        source = 'cache'

    try:
        shutil.copy(translated_subs, error_dir)
    except OSError:
        pass

    with open(os.path.join(error_dir, 'manifest.json'), 'w') as f:
        json.dump({'video_file': playing_file,
                   'playtime': "{}:{:02}:{:02}".format(int(play_time / 3600), int((play_time % 3600)/60), play_time % 60),
                   'orig_subs': orig_subs,
                   'translated_subs': translated_subs,
                   'source': source,
                   'version': __version__},
                  f, indent=4)

    xbmcgui.Dialog().notification(addon_info.localise(30100),
                                  addon_info.localise(30901),
                                  sound=False)

def extract_progress_info(line):
    if "time=" not in line:
        return None
    progress_data = {}
    parts = line.strip().split()
    for part in parts:
        if '=' in part:
            key, value = part.split('=')
            progress_data[key] = value
    return progress_data

def calculate_progress_percentage(progress_info, total_duration):
    time_str = progress_info.get('time', '0:00:00.00')
    current_time = duration_str_to_seconds(time_str)
    return (current_time / total_duration) * 100

def get_video_duration(video_file):
    command = ['ffmpeg', '-i', video_file]
    result = subprocess.run(command, stderr=subprocess.PIPE, text=True)
    for line in result.stderr.splitlines():
        if "Duration" in line:
            duration_str = line.split("Duration: ")[1].split(",")[0]
            return duration_str_to_seconds(duration_str)
    return None

def duration_str_to_seconds(duration_str):
    h, m, s = duration_str.split(':')
    s, ms = s.split('.')
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 100


def extract_subtitles(video_file:str, output_file:str, language:str)->bool:
    total_duration = get_video_duration(video_file)
    if not total_duration:
        logger.error("Could not determine video duration.")
        total_duration = 60*60

    command = [
        'ffmpeg',
        '-i', video_file,
        '-map', f'0:s:m:language:{Language(language).alpha3}',
        output_file,
        '-y'
    ]
    logger.debug(f"Attempting to extract subtitle {language=} from {video_file=} into {output_file=}...")
    try:
        # Start the ffmpeg process
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        dialog = xbmcgui.Dialog()

        while process.poll() is None:
            line = process.stdout.readline()
            # logger.debug(line)
            progress_info = extract_progress_info(line)
            if progress_info:
                logger.debug(f"{progress_info=}")
                progress_percent = calculate_progress_percentage(progress_info, total_duration)
                dialog.notification("Subtitle Extraction", f"Progress: {int(progress_percent)}%", time=1000)

        # Wait for process to complete and check the return code
        process.communicate()
        if process.returncode != 0:
            logger.error(f"ffmpeg returned non-zero exit status: {process.returncode}")
            return False
        
        logger.debug(f"Subtitles extracted to {output_file}")
        dialog.notification("Subtitle Extraction", "Completed", time=3000)
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"An error occurred: {e}\n{e.output}\n{e.stderr}\n{e.stdout}")
        return False
    
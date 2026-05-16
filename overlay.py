#!/usr/bin/env python3
import PyQt5  # type: ignore[reportMissingImports]
from PyQt5 import QtWidgets, QtCore, QtGui  # type: ignore[reportMissingImports]
import PyQt5.QtMultimedia as QtMultimedia  # type: ignore[reportMissingImports]
import threading
import time
try:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager  # type: ignore[reportMissingImports]
    from winrt.windows.foundation import TimeSpan  # type: ignore[reportMissingImports]
except Exception:
    GlobalSystemMediaTransportControlsSessionManager = None
    TimeSpan = None
import sys
import os


class MediaWatcher(PyQt5.QtCore.QThread):
    media_changed = PyQt5.QtCore.pyqtSignal(str, str, str, bool, bool)

    def __init__(self, interval=1.0):
        super().__init__()
        self.interval = interval
        self._running = True

    def run(self):
        last = (None, None, None, None)

        # If WinRT SMTC is available, prefer it; otherwise use window-title fallback.
        if GlobalSystemMediaTransportControlsSessionManager is not None:
            try:
                mgr = GlobalSystemMediaTransportControlsSessionManager.request_async().get()
            except Exception:
                mgr = None

            while self._running:
                try:
                    chosen = None
                    if mgr is not None:
                        sessions = mgr.get_sessions()
                        for s in sessions:
                            try:
                                props = s.try_get_media_properties_async().get()
                                title = props.title
                                artist = ", ".join(props.artist) if hasattr(props, 'artist') else getattr(props, 'artist', '')
                                appid = s.source_app_user_model_id or ""
                                info = s.get_playback_info()
                                is_playing = info.playback_status.name == 'Playing'
                                if title:
                                    if 'yandex' in appid.lower() or 'yandex' in (artist or '').lower():
                                        chosen = (title, artist, getattr(props, 'album', ''), is_playing, True)
                                        break
                                    if chosen is None:
                                        chosen = (title, artist, getattr(props, 'album', ''), is_playing, True)
                            except Exception:
                                continue

                    # fallback to window titles if SMTC returns a generic Yandex page title
                    if chosen is None or self._is_generic_media_title(chosen[0], chosen[1]):
                        scanned = self._scan_windows()
                        if scanned[0]:
                            chosen_album = chosen[2] if chosen is not None else ''
                            chosen_playing = chosen[3] if chosen is not None else scanned[3]
                            chosen_seek = chosen[4] if chosen is not None else scanned[4]
                            chosen = (
                                scanned[0],
                                scanned[1],
                                chosen_album,
                                chosen_playing,
                                chosen_seek,
                            )
                        elif chosen is None:
                            chosen = scanned

                    if chosen != last:
                        last = chosen
                        print('MediaWatcher emit', chosen)
                        self.media_changed.emit(
                            chosen[0] or '',
                            chosen[1] or '',
                            chosen[2] or '',
                            bool(chosen[3]),
                            bool(chosen[4] if len(chosen) > 4 else False),
                        )
                except Exception:
                    pass
                time.sleep(self.interval)
        else:
            # winrt not installed — use window title scan
            while self._running:
                try:
                    chosen = self._scan_windows()
                    if chosen != last:
                        last = chosen
                        print('MediaWatcher emit', chosen)
                        self.media_changed.emit(
                            chosen[0] or '',
                            chosen[1] or '',
                            chosen[2] or '',
                            bool(chosen[3]),
                            bool(chosen[4] if len(chosen) > 4 else False),
                        )
                except Exception:
                    pass
                time.sleep(self.interval)

    def _scan_windows(self):
        # scan top-level windows for titles mentioning Yandex Music (browser tabs may be hidden/minimized)
        import ctypes
        from ctypes import wintypes
        import re

        user32 = ctypes.WinDLL('user32', use_last_error=True)

        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        GetWindowTextW = user32.GetWindowTextW

        titles = []

        def foreach(hwnd, lParam):
            length = GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                GetWindowTextW(hwnd, buf, length + 1)
                txt = buf.value
                if txt:
                    titles.append(txt)
            return True

        EnumWindows(EnumWindowsProc(foreach), 0)

        # look for Yandex Music or browser titles containing music info
        keywords = ['яндекс', 'yandex', 'музыка', 'music']
        generic = {
            'яндекс музыка', 'yandex music', 'яндекс', 'yandex', 'музыка', 'music', 'собираем музыку для вас',
            'яндекс.мюзыка', 'яндекс.музыка', 'yandex.music'
        }
        ignore_phrases = [
            'яндекс браузер', 'yandex browser', 'собираем музыку для вас',
            'яндекс музыка', 'yandex music', 'яндекс.музыка', 'yandex.music'
        ]

        def normalize_text(value: str) -> str:
            return re.sub(r'\s+', ' ', value.strip().lower())

        def is_generic_text(value: str) -> bool:
            return normalize_text(value) in generic

        def is_ignored_part(value: str) -> bool:
            low = normalize_text(value)
            return any(phrase in low for phrase in ignore_phrases) or is_generic_text(low)

        def strip_trailing_generic(value: str) -> str:
            text = normalize_text(value)
            for phrase in ignore_phrases:
                if text.endswith(phrase):
                    stripped = value[: -len(phrase)].rstrip(' -–—|. ')
                    return stripped
            return value

        def clean_title(value: str):
            stripped = strip_trailing_generic(value).strip()
            if stripped and not is_generic_text(stripped):
                return stripped
            return None

        def parse_title(title: str):
            title = clean_title(title)
            if not title:
                return None, None
            sep = re.compile(r'\s*[—–\-|]\s*')
            raw_parts = [p.strip() for p in sep.split(title) if p.strip()]
            parts = [p for p in raw_parts if not is_ignored_part(p)]
            if len(parts) >= 3:
                return parts[-1], parts[-2]
            if len(parts) == 2:
                return parts[0], parts[1]
            if len(parts) == 1:
                return parts[0], ''
            return None, None

        candidates = []
        for t in titles:
            low = normalize_text(t)
            if any(k in low for k in keywords) or low.endswith('яндекс музыка') or low.endswith('yandex music'):
                if not is_generic_text(low):
                    candidates.append(t)

        if candidates:
            print('MediaWatcher window candidates:', candidates)
            # store last candidates for debugging/UI
            try:
                self.last_candidates = candidates
            except Exception:
                pass
            for candidate in candidates:
                title, artist = parse_title(candidate)
                if title:
                    return (title, artist, '', True, False)
                fallback = clean_title(candidate)
                if fallback:
                    return (fallback, '', '', True, False)

        return (None, None, None, False, False)

    def _is_generic_media_title(self, title, artist):
        if not title:
            return True
        low = title.strip().lower()
        generic = [
            'яндекс музыка',
            'yandex music',
            'собираем музыку для вас',
            'яндекс браузер',
            'yandex browser',
        ]
        if any(phrase in low for phrase in generic):
            return True
        return False


class Overlay(PyQt5.QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.player = PyQt5.QtMultimedia.QMediaPlayer()
        self.player.positionChanged.connect(self.on_position_changed)
        self.player.durationChanged.connect(self.on_duration_changed)
        self.player.stateChanged.connect(self.on_state_changed)
        self.external_present = False
        self.external_seek_supported = False
        self.init_ui()

    def init_ui(self):
        # frameless, always on top; keep as normal window so it accepts input
        self.setWindowFlags(
            PyQt5.QtCore.Qt.FramelessWindowHint
            | PyQt5.QtCore.Qt.WindowStaysOnTopHint
            | PyQt5.QtCore.Qt.Window
        )
        self.setAttribute(PyQt5.QtCore.Qt.WA_TranslucentBackground)
        w, h = 380, 150
        self.setFixedSize(w, h)

        # use the main widget as styled container so events propagate correctly
        self.setObjectName("container")
        self.setStyleSheet(
            """
        QWidget#container {
            background-color: rgba(0, 0, 0, 255);
            border: 1px solid rgba(255, 255, 255, 12%);
            border-radius: 14px;
            color: #f5f5f5;
        }
        QPushButton {
            min-width: 72px;
            min-height: 28px;
            border: 1px solid rgba(255, 255, 255, 30%);
            border-radius: 8px;
            background-color: rgba(255, 255, 255, 8%);
            color: white;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: rgba(255, 255, 255, 14%);
        }
        QPushButton:pressed {
            background-color: rgba(255, 255, 255, 24%);
        }
        QLabel {
            color: #f0f0f0;
        }
        QSlider::groove:horizontal {
            height: 8px;
            background: rgba(255, 255, 255, 20%);
            border-radius: 4px;
        }
        QSlider::handle:horizontal {
            width: 14px;
            background: #ffffff;
            margin: -3px 0;
            border-radius: 7px;
        }
        QSlider::sub-page:horizontal {
            background: qlineargradient(spread:pad, x1:0, y1:0.5, x2:1, y2:0.5, stop:0 rgba(80, 185, 255, 200), stop:1 rgba(80, 185, 255, 120));
            border-radius: 4px;
        }
        """
        )

        layout = PyQt5.QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        top_row = PyQt5.QtWidgets.QHBoxLayout()
        self.prev_btn = PyQt5.QtWidgets.QPushButton("Prev")
        self.play_btn = PyQt5.QtWidgets.QPushButton("Play")
        self.pause_btn = PyQt5.QtWidgets.QPushButton("Pause")
        self.next_btn = PyQt5.QtWidgets.QPushButton("Next")
        top_row.addWidget(self.prev_btn)
        top_row.addWidget(self.play_btn)
        top_row.addWidget(self.pause_btn)
        top_row.addWidget(self.next_btn)
        top_row.addStretch()
        layout.addLayout(top_row)

        self.track_label = PyQt5.QtWidgets.QLabel("No file")
        self.track_label.setMinimumHeight(40)
        self.track_label.setWordWrap(True)
        self.track_label.setSizePolicy(PyQt5.QtWidgets.QSizePolicy.Expanding, PyQt5.QtWidgets.QSizePolicy.Preferred)
        self.track_label.setTextInteractionFlags(PyQt5.QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.track_label)

        self.status_label = PyQt5.QtWidgets.QLabel("Stopped")
        self.status_label.setStyleSheet("color: rgba(180, 220, 255, 210); font-size: 12px;")
        layout.addWidget(self.status_label)

        self.pos_slider = PyQt5.QtWidgets.QSlider(PyQt5.QtCore.Qt.Horizontal)
        self.pos_slider.setRange(0, 0)
        self.pos_slider.setEnabled(True)
        self.pos_slider.setTracking(True)
        self.pos_slider.setMouseTracking(True)
        self.pos_slider.setAttribute(PyQt5.QtCore.Qt.WA_TransparentForMouseEvents, False)
        self.pos_slider.valueChanged.connect(lambda v: print('slider valueChanged', v))
        layout.addWidget(self.pos_slider)

        bottom_row = PyQt5.QtWidgets.QHBoxLayout()
        self.time_label = PyQt5.QtWidgets.QLabel("00:00 / 00:00")
        bottom_row.addWidget(self.time_label)
        bottom_row.addStretch()
        layout.addLayout(bottom_row)

        # connections
        self.prev_btn.clicked.connect(self.prev_track)
        self.play_btn.clicked.connect(self.toggle_play)
        self.pause_btn.clicked.connect(self.pause_playback)
        self.next_btn.clicked.connect(self.next_track)
        # slider interactions: store press value and handle release for external fallback
        self.pos_slider.sliderMoved.connect(self._on_slider_moved)
        self.pos_slider.sliderPressed.connect(self._on_slider_pressed)
        self.pos_slider.sliderReleased.connect(self._on_slider_released)
        # install event filter to capture raw mouse events for debugging
        self.pos_slider.installEventFilter(self)

        # ensure widgets accept focus and mouse events
        for widget in (self.prev_btn, self.play_btn, self.pause_btn, self.next_btn, self.pos_slider):
            widget.setFocusPolicy(PyQt5.QtCore.Qt.StrongFocus)
        self.setAttribute(PyQt5.QtCore.Qt.WA_TransparentForMouseEvents, False)

        # position window top-right with small margin
        screen = PyQt5.QtWidgets.QApplication.primaryScreen().availableGeometry()
        x = screen.right() - w - 10
        y = screen.top() + 10
        self.move(x, y)

        # allow dragging by mouse
        self._drag_pos = None

        # start media watcher thread (Windows SMTC via winrt)
        self.media_watcher = MediaWatcher()
        self.media_watcher.media_changed.connect(self.on_external_media_changed)
        self.media_watcher.start()

        self.external_time_enabled = GlobalSystemMediaTransportControlsSessionManager is not None
        if self.external_time_enabled:
            self.external_timer = PyQt5.QtCore.QTimer(self)
            self.external_timer.setInterval(1000)
            self.external_timer.timeout.connect(self._update_external_state)
            self.external_timer.start()
        else:
            self.external_timer = None

        # slider interaction state
        self._slider_pressed_value = None

    def mousePressEvent(self, event):
        print('mousePressEvent', event.pos(), 'button', event.button())
        if event.button() == PyQt5.QtCore.Qt.LeftButton:
            pos = event.pos()
            # if click inside slider, handle as slider press
            try:
                slider_rect = self.pos_slider.geometry()
                print('slider rect', slider_rect)
                # expand hit area to account for layout/painting offsets
                exp_rect = slider_rect.adjusted(-8, -24, 8, 36)
                print('expanded slider rect', exp_rect)
                if exp_rect.contains(pos):
                    # compute value from click position (clamped to expanded rect)
                    x = max(exp_rect.left(), min(pos.x(), exp_rect.right())) - slider_rect.x()
                    w = max(1, slider_rect.width())
                    ratio = max(0.0, min(1.0, x / w))
                    val = int(self.pos_slider.minimum() + ratio * (self.pos_slider.maximum() - self.pos_slider.minimum()))
                    try:
                        self.pos_slider.setValue(val)
                        self._slider_pressed_value = val
                        print('mousePressEvent -> slider click', val)
                    except Exception:
                        pass
                    self._slider_clicking = True
                    return
            except Exception:
                pass
            # otherwise treat as drag for moving window
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        # if currently dragging the slider area, update slider value
        try:
            if getattr(self, '_slider_clicking', False):
                pos = event.pos()
                slider_rect = self.pos_slider.geometry()
                exp_rect = slider_rect.adjusted(-8, -24, 8, 36)
                if exp_rect.contains(pos):
                    x = max(exp_rect.left(), min(pos.x(), exp_rect.right())) - slider_rect.x()
                    w = max(1, slider_rect.width())
                    ratio = max(0.0, min(1.0, x / w))
                    val = int(self.pos_slider.minimum() + ratio * (self.pos_slider.maximum() - self.pos_slider.minimum()))
                    self.pos_slider.setValue(val)
                    print('mouseMoveEvent -> slider drag', val)
                    return
        except Exception:
            pass
        if self._drag_pos is not None and event.buttons() & PyQt5.QtCore.Qt.LeftButton:
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        # if we were interacting with slider via mouse, finish that
        if getattr(self, '_slider_clicking', False):
            try:
                val = int(self.pos_slider.value())
                print('mouseReleaseEvent -> slider release', val)
                # call our release handler
                self._on_slider_released()
            except Exception:
                pass
            self._slider_clicking = False
            return
        self._drag_pos = None

    def on_external_media_changed(self, title, artist, album, is_playing, can_seek):
        # update UI from external media (Yandex Music or other SMTC session)
        self.external_present = bool(title)
        self.external_seek_supported = bool(can_seek)
        # Always allow the slider to be used locally; if external SMTC seeking
        # is not available we'll present a centered relative slider so the
        # user can drag to request relative seeking.
        self.pos_slider.setEnabled(True)
        if title:
            text = f"{artist} — {title}" if artist else title
            self.track_label.setText(text)
            self.track_label.setVisible(True)
            self.track_label.setToolTip(text)
        elif not self.player.media().isNull():
            self.track_label.setText("No file")
            self.track_label.setToolTip("")
        self.status_label.setText("Playing" if is_playing else "Paused")
        if self.external_time_enabled:
            self._update_external_time()
            # if SMTC is available but the session doesn't support seek,
            # present a centered relative slider so user can request forward/back
            if self.external_present and not self.external_seek_supported:
                self.pos_slider.setRange(0, 1000)
                self.pos_slider.setValue(500)
                print('external SMTC no seek -> set pos_slider range to', self.pos_slider.minimum(), self.pos_slider.maximum())
        else:
            # if external media present but SMTC doesn't support seek, provide
            # a relative slider so user can request forward/back via arrow keys
            if self.external_present and not self.external_seek_supported:
                if self.pos_slider.maximum() <= 1:
                    self.pos_slider.setRange(0, 1000)
                    # center slider so movement to right/left indicates relative seek
                    self.pos_slider.setValue(500)
                    print('set pos_slider range to', self.pos_slider.minimum(), self.pos_slider.maximum())
            # show candidates or fallback text if no title was parsed
            if not title:
                try:
                    candidates = getattr(self.media_watcher, 'last_candidates', None)
                    if candidates:
                        fallback = candidates[0]
                        self.track_label.setText(fallback)
                        self.track_label.setToolTip(fallback)
                        self.status_label.setText(f"No title — candidates: {fallback}")
                    else:
                        self.status_label.setText("No title detected")
                except Exception:
                    self.status_label.setText("No title detected")

    def toggle_play(self):
        print('toggle_play clicked')
        has_local_media = not self.player.media().isNull()
        if self.external_present:
            try:
                self.control_system_play_pause()
            except Exception:
                self._send_media_key()
            return

        if has_local_media:
            self.player.play()
        else:
            self._send_media_key()

    def pause_playback(self):
        print('pause_playback clicked')
        has_local_media = not self.player.media().isNull()
        if self.external_present:
            session = self._get_system_session()
            if session is not None:
                try:
                    session.try_pause_async().get()
                    return
                except Exception:
                    pass
            self._send_media_key()
            return

        if has_local_media:
            self.player.pause()
        else:
            self._send_media_key()

    def control_system_play_pause(self):
        # Try WinRT SMTC control first
        if GlobalSystemMediaTransportControlsSessionManager is not None:
            mgr = GlobalSystemMediaTransportControlsSessionManager.request_async().get()
            sessions = mgr.get_sessions()
            # prefer session with yandex in id or first active
            chosen = None
            for s in sessions:
                try:
                    appid = s.source_app_user_model_id or ""
                    props = s.try_get_media_properties_async().get()
                    title = props.title
                    info = s.get_playback_info()
                    if title:
                        if 'yandex' in appid.lower() or 'yandex' in (title or '').lower():
                            chosen = s
                            break
                        if chosen is None:
                            chosen = s
                except Exception:
                    continue

            if chosen is not None:
                info = chosen.get_playback_info()
                status = info.playback_status.name
                if status == 'Playing':
                    chosen.try_pause_async().get()
                else:
                    chosen.try_play_async().get()
                return

        # fallback: send media key if WinRT control is unavailable
        self._send_media_key()

    def _send_app_command(self, app_command):
        try:
            import ctypes
            user32 = ctypes.WinDLL('user32', use_last_error=True)
            HWND_BROADCAST = 0xFFFF
            WM_APPCOMMAND = 0x0319
            user32.SendMessageW(HWND_BROADCAST, WM_APPCOMMAND, 0, app_command << 16)
            return True
        except Exception:
            return False

    def _send_media_key(self, vk_code=0xB3):
        try:
            import ctypes
            user32 = ctypes.WinDLL('user32')
            KEYEVENTF_KEYUP = 0x0002
            user32.keybd_event(vk_code, 0, 0, 0)
            time.sleep(0.05)
            user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)
            return True
        except Exception:
            return False

    def next_track(self):
        print('next_track clicked')
        if self.external_present:
            session = self._get_system_session()
            if session is not None:
                try:
                    session.try_skip_next_async().get()
                    return
                except Exception:
                    pass
            self._send_media_key(0xB0)
            return
        self._send_media_key(0xB0)

    def prev_track(self):
        print('prev_track clicked')
        if self.external_present:
            session = self._get_system_session()
            if session is not None:
                try:
                    session.try_skip_previous_async().get()
                    return
                except Exception:
                    pass
            self._send_media_key(0xB1)
            return
        self._send_media_key(0xB1)

    def on_position_changed(self, pos):
        # pos and duration are in ms
        self.pos_slider.blockSignals(True)
        self.pos_slider.setValue(pos)
        self.pos_slider.blockSignals(False)
        self.update_time_label()

    def on_duration_changed(self, dur):
        self.pos_slider.setRange(0, dur)
        self.update_time_label()

    def update_time_label(self):
        pos = self.player.position()
        dur = self.player.duration()

        def fmt(ms):
            s = int(ms / 1000) if ms is not None else 0
            m = s // 60
            s = s % 60
            return f"{m:02d}:{s:02d}"

        self.time_label.setText(f"{fmt(pos)} / {fmt(dur)}")

    def seek(self, ms):
        print('seek', ms, 'external', self.external_present, 'seek_supported', self.external_seek_supported)
        if self.external_present and self.external_seek_supported:
            session = self._get_system_session()
            if session is not None:
                try:
                    if TimeSpan is not None:
                        session.try_change_playback_position_async(TimeSpan(ms * 10000)).get()
                    else:
                        session.try_change_playback_position_async(ms).get()
                    return
                except Exception:
                    pass
        # If external media is present but SMTC doesn't expose seek, do nothing here.
        # Relative seeking fallback is handled on slider release (_on_slider_released).
        self.player.setPosition(ms)

    def _on_slider_pressed(self):
        try:
            self._slider_pressed_value = int(self.pos_slider.value())
            print('slider pressed', self._slider_pressed_value)
        except Exception:
            self._slider_pressed_value = None

    def _on_slider_moved(self, value):
        # while dragging, update time label for local player preview
        try:
            print('slider moved', value)
            if not self.external_present:
                # show preview time based on slider value
                dur = self.player.duration()
                if dur > 0:
                    self.time_label.setText(f"{self._format_time(value)} / {self._format_time(dur)}")
        except Exception:
            pass

    def _on_slider_released(self):
        released = int(self.pos_slider.value())
        print('slider released', released)
        # external present without SMTC seek -> interpret relative move
        if self.external_present and not self.external_seek_supported:
            start = self._slider_pressed_value if self._slider_pressed_value is not None else 500
            delta = released - start
            # each 10 slider units == one 5s step
            step_units = 10
            steps = int(delta / step_units)
            # ensure at least one step if there was a non-zero delta
            if steps == 0 and delta != 0:
                steps = 1 if delta > 0 else -1
            if steps != 0:
                hwnd = self._find_window_by_keywords(['яндекс музыка', 'yandex music', 'yandexmusic', 'яндексmusic', 'яндекс музыка'])
                print('relative seek steps', steps, 'found hwnd', hwnd)
                if hwnd is not None:
                    vk = 0x27 if steps > 0 else 0x25
                    print('sending key', hex(vk), 'count', abs(steps))
                    self._send_key_sequence_to_window(hwnd, vk, abs(steps))
                    # reset slider center
                    try:
                        self.pos_slider.setValue(500)
                    except Exception:
                        pass
                    self._slider_pressed_value = None
                    return
        # otherwise perform normal seek
        try:
            self.seek(released)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        try:
            from PyQt5.QtCore import QEvent
            if obj is self.pos_slider:
                if event.type() == QEvent.MouseButtonPress:
                    print('eventFilter: MouseButtonPress')
                elif event.type() == QEvent.MouseMove:
                    print('eventFilter: MouseMove')
                elif event.type() == QEvent.MouseButtonRelease:
                    print('eventFilter: MouseButtonRelease')
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _find_window_by_keywords(self, keywords):
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL('user32', use_last_error=True)
            EnumWindows = user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            GetWindowTextLengthW = user32.GetWindowTextLengthW
            GetWindowTextW = user32.GetWindowTextW

            found = []

            def foreach(hwnd, lParam):
                length = GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    GetWindowTextW(hwnd, buf, length + 1)
                    txt = buf.value
                    if txt:
                        low = txt.lower()
                        for k in keywords:
                            if k in low:
                                found.append(hwnd)
                                return False
                return True

            EnumWindows(EnumWindowsProc(foreach), 0)
            return found[0] if found else None
        except Exception:
            return None

    def _send_key_sequence_to_window(self, hwnd, vk_code, count):
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.WinDLL('user32', use_last_error=True)

            # try to bring the window to foreground
            try:
                user32.ShowWindow(hwnd, 5)  # SW_SHOW
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

            KEYEVENTF_KEYUP = 0x0002
            for _ in range(max(1, int(count))):
                user32.keybd_event(vk_code, 0, 0, 0)
                time.sleep(0.02)
                user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)
                time.sleep(0.02)
            return True
        except Exception:
            return False

    def _get_system_session(self):
        if GlobalSystemMediaTransportControlsSessionManager is None:
            return None
        try:
            mgr = GlobalSystemMediaTransportControlsSessionManager.request_async().get()
            sessions = mgr.get_sessions()
            chosen = None
            for s in sessions:
                try:
                    props = s.try_get_media_properties_async().get()
                    title = props.title
                    appid = s.source_app_user_model_id or ""
                    if title:
                        if 'yandex' in appid.lower() or 'yandex' in title.lower():
                            chosen = s
                            break
                        if chosen is None:
                            chosen = s
                except Exception:
                    continue
            return chosen
        except Exception:
            return None

    def _timespan_to_millis(self, value):
        if value is None:
            return None
        try:
            if isinstance(value, int):
                return int(value / 10000)
            return int(value.total_seconds() * 1000)
        except Exception:
            try:
                return int(value / 10000)
            except Exception:
                return None

    def _update_external_time(self):
        if not self.external_present:
            return
        session = self._get_system_session()
        if session is None:
            return
        try:
            timeline = session.try_get_timeline_properties_async().get()
            pos = self._timespan_to_millis(getattr(timeline, 'position', None))
            duration = self._timespan_to_millis(getattr(timeline, 'end_time', None))
            if duration is None:
                duration = self._timespan_to_millis(getattr(timeline, 'max_seek_time', None))
            if pos is not None and duration is not None:
                self.pos_slider.setRange(0, duration)
                self.pos_slider.blockSignals(True)
                self.pos_slider.setValue(pos)
                self.pos_slider.blockSignals(False)
                self.time_label.setText(f"{self._format_time(pos)} / {self._format_time(duration)}")
        except Exception:
            pass

    def _update_external_state(self):
        if self.external_present and self.external_time_enabled:
            self._update_external_time()

    def _format_time(self, ms):
        s = int(ms / 1000) if ms is not None else 0
        m = s // 60
        s = s % 60
        return f"{m:02d}:{s:02d}"

    def on_state_changed(self, state):
        self.status_label.setText("Playing" if state == PyQt5.QtMultimedia.QMediaPlayer.PlayingState else "Paused")


if __name__ == "__main__":
    PyQt5.QtWidgets.QApplication.setAttribute(PyQt5.QtCore.Qt.AA_EnableHighDpiScaling, True)
    app = PyQt5.QtWidgets.QApplication(sys.argv)
    overlay = Overlay()
    overlay.show()
    sys.exit(app.exec_())

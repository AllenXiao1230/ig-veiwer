"""Embedded account preview and system-browser navigation."""

from __future__ import annotations

import sys
from urllib.parse import quote

from PySide6.QtCore import QUrl, QUrlQuery, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import (
    QWebEngineLoadingInfo, QWebEngineNavigationRequest, QWebEnginePage,
    QWebEngineProfile, QWebEngineWebAuthUxRequest,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout

from .storage import application_data_dir

INSTAGRAM_LOGIN_URL = "https://www.instagram.com/accounts/login/"


def profile_url(username: str) -> str:
    return f"https://www.instagram.com/{quote(username.strip(), safe='._')}/"


class ProfilePreview(QGroupBox):
    message = Signal(str)
    profile_url = staticmethod(profile_url)

    def __init__(self, parent=None):
        super().__init__("帳號預覽", parent)
        self.preview_profile_url: QUrl | None = None
        self.profile_browser: QWebEngineView | None = None
        self.preview_login_required = False
        self.setMinimumWidth(280)
        browser_layout = QVBoxLayout(self)
        browser_layout.setContentsMargins(6, 8, 6, 6)
        browser_actions = QHBoxLayout()
        login_button = QPushButton("登入 Instagram")
        login_button.setToolTip(
            "在系統瀏覽器開啟 Instagram 官方登入頁"
            if sys.platform == "win32"
            else "在帳號預覽中開啟 Instagram 官方登入頁"
        )
        login_button.clicked.connect(self.open_instagram_login)
        browser_actions.addWidget(login_button)
        external_button = QPushButton("在瀏覽器開啟")
        external_button.setToolTip("以系統瀏覽器開啟預覽帳號或 Instagram 登入頁")
        external_button.clicked.connect(self.open_current_profile_externally)
        browser_actions.addWidget(external_button)
        browser_actions.addStretch()
        browser_layout.addLayout(browser_actions)

        browser_toolbar = QHBoxLayout()
        self.btn_browser_back = QPushButton("上一頁")
        self.btn_browser_back.setAccessibleName("預覽上一頁")
        self.btn_browser_back.setEnabled(False)
        browser_toolbar.addWidget(self.btn_browser_back)
        self.btn_browser_refresh = QPushButton("重新整理")
        self.btn_browser_refresh.setAccessibleName("重新整理帳號預覽")
        self.btn_browser_refresh.setEnabled(False)
        browser_toolbar.addWidget(self.btn_browser_refresh)
        browser_toolbar.addStretch()
        browser_layout.addLayout(browser_toolbar)
        self.browser_status = QLabel(
            "選取帳號以預覽。"
            if sys.platform == "win32"
            else "選取帳號以預覽，或先登入 Instagram。"
        )
        self.browser_status.setWordWrap(True)
        browser_layout.addWidget(self.browser_status)
        self.browser_stack = QStackedWidget()
        browser_layout.addWidget(self.browser_stack, 1)
        if sys.platform == "win32":
            session_notice = QLabel("登入使用系統瀏覽器，帳號預覽另存登入狀態。完成登入後可按「在瀏覽器開啟」查看帳號。")
            session_notice.setWordWrap(True)
            browser_layout.addWidget(session_notice)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.preview_profile_url is not None and (self.profile_browser is None or self.profile_browser.url() != self.preview_profile_url):
            self.navigate_preview(self.preview_profile_url)

    def load_profile_in_browser(self, username: str) -> None:
        self.preview_profile_url = QUrl(self.profile_url(username))
        if self.isVisible():
            self.navigate_preview(self.preview_profile_url)

    def open_instagram_login(self) -> None:
        url = QUrl(INSTAGRAM_LOGIN_URL)
        if self.preview_profile_url is not None:
            query = QUrlQuery()
            query.addQueryItem("next", self.preview_profile_url.path(QUrl.ComponentFormattingOption.FullyEncoded))
            url.setQuery(query)
        if sys.platform == "win32":
            self.open_in_system_browser(url)
        else:
            self.navigate_preview(url)

    def navigate_preview(self, url: QUrl) -> None:
        if self.profile_browser is None:
            browser_data_dir = application_data_dir() / "browser-profile"
            self.browser_profile = QWebEngineProfile("Instagram", self)
            self.browser_profile.setPersistentStoragePath(str(browser_data_dir))
            self.browser_profile.setCachePath(str(browser_data_dir / "cache"))
            self.profile_browser = QWebEngineView()
            self.profile_browser.setAccessibleName("Instagram 帳號預覽")
            page = QWebEnginePage(self.browser_profile, self.profile_browser)
            self.browser_profile.setParent(page)
            self.profile_browser.setPage(page)
            page.loadingChanged.connect(self.on_preview_loading_changed)
            if sys.platform == "win32":
                page.navigationRequested.connect(self.on_preview_navigation_requested)
                page.webAuthUxRequested.connect(self.on_preview_webauth_requested)
            self.btn_browser_back.clicked.connect(self.profile_browser.back)
            self.btn_browser_refresh.clicked.connect(self.profile_browser.reload)
            self.btn_browser_refresh.setEnabled(True)
            self.browser_stack.addWidget(self.profile_browser)
        self.profile_browser.setUrl(url)

    @staticmethod
    def is_instagram_auth_page(url: QUrl) -> bool:
        host = url.host()
        if host in {"auth.meta.com", "accountscenter.instagram.com", "accountscenter.facebook.com"}:
            return True
        path = url.path().rstrip("/") + "/"
        return host in {"instagram.com", "www.instagram.com"} and path.startswith(
            ("/accounts/login/", "/accounts/two_factor/", "/accounts/onetap/", "/challenge/", "/checkpoint/")
        )

    def show_preview_login_required(self) -> None:
        self.preview_login_required = True
        self.browser_status.setText("此預覽需要驗證。請按「登入 Instagram」，並在瀏覽器完成驗證。")

    def on_preview_navigation_requested(self, request: QWebEngineNavigationRequest) -> None:
        if request.isMainFrame():
            self.preview_login_required = False
            if self.is_instagram_auth_page(request.url()):
                self.show_preview_login_required()
                request.reject()

    def on_preview_webauth_requested(self, request: QWebEngineWebAuthUxRequest) -> None:
        self.show_preview_login_required()
        request.cancel()

    def on_preview_loading_changed(self, info: QWebEngineLoadingInfo) -> None:
        status = info.status()
        load_status = QWebEngineLoadingInfo.LoadStatus
        if status == load_status.LoadStoppedStatus:
            return
        self.btn_browser_back.setEnabled(self.profile_browser.history().canGoBack())
        if self.preview_login_required:
            return
        if status == load_status.LoadStartedStatus:
            self.browser_status.setText("正在載入 Instagram…")
        elif status == load_status.LoadFailedStatus:
            self.browser_status.setText("無法載入 Instagram。請重新整理，或在瀏覽器開啟。")
        else:
            if self.is_instagram_auth_page(info.url()):
                self.browser_status.setText("請在 Instagram 完成登入或驗證。若無法繼續，請在瀏覽器開啟。")
            else:
                self.browser_status.clear()

    def open_current_profile_externally(self) -> None:
        self.open_in_system_browser(self.preview_profile_url or QUrl(INSTAGRAM_LOGIN_URL))

    def open_in_system_browser(self, url: QUrl) -> None:
        if not QDesktopServices.openUrl(url):
            self.message.emit("無法開啟瀏覽器，請確認系統已設定預設瀏覽器。")


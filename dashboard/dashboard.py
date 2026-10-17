#!/usr/bin/env python3
"""Tiny dependency-free status dashboard for the Raspberry Pi."""

from __future__ import annotations

import glob
import json
import os
import platform
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from project_monitor import ProjectMonitor


HOST = "127.0.0.1"
PORT = 8080
SAMPLE_INTERVAL = 3.0
SESSION_COOKIE = "__Host-pi_admin"
ASSET_DIR = Path(__file__).resolve().parent / "assets"
PUBLIC_ASSETS = {
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/favicon.ico": ("favicon.ico", "image/x-icon"),
    "/apple-touch-icon.png": ("apple-touch-icon.png", "image/png"),
    "/share-cover.png": ("share-cover-source.png", "image/png"),
}
PROJECTS = ProjectMonitor()


HTML = r'''<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <meta name="theme-color" content="#16211d">
  <meta name="description" content="Живая телеметрия домашнего Raspberry Pi сервера">
  <meta name="theme-color" content="#16211d">
  <link rel="icon" href="/favicon.svg?v=1" type="image/svg+xml">
  <link rel="alternate icon" href="/favicon.ico?v=1" sizes="256x256">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png?v=1" sizes="180x180">
  <link rel="canonical" href="__PAGE_URL__">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="PI Hardbox">
  <meta property="og:title" content="PI Hardbox — состояние сервера">
  <meta property="og:description" content="Состояние Raspberry Pi, проекты, VPN и управление сервером.">
  <meta property="og:url" content="__PAGE_URL__">
  <meta property="og:image" content="https://api.pi-hardbox.ru/share-cover.png?v=1">
  <meta property="og:image:type" content="image/png">
  <meta property="og:image:width" content="1733">
  <meta property="og:image:height" content="907">
  <meta property="og:image:alt" content="PI Hardbox — домашний сервер Raspberry Pi">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="PI Hardbox — состояние сервера">
  <meta name="twitter:image" content="https://api.pi-hardbox.ru/share-cover.png?v=1">
  <title>PI Hardbox — состояние сервера</title>
  <link rel="stylesheet" href="/style.css?v=img2txt1">
</head>
<body data-page="server">
  <main class="shell">
    <header class="masthead">
      <a class="brand" href="/" aria-label="PI Hardbox, состояние сервера">
        <span class="brand-mark" aria-hidden="true">PI</span>
        <span>HARD<span>BOX</span></span>
      </a>
      <div class="live" role="status" aria-live="polite">
        <span class="live-dot" aria-hidden="true"></span>
        <span id="connection">Подключение</span>
        <span class="live-rate">/ 3 сек</span>
      </div>
    </header>

    <nav class="section-tabs" aria-label="Разделы панели">
      <a href="/" data-page-link="server">Дашборд</a>
      <a href="/vpn" data-page-link="vpn">VPN</a>
      <a href="/manage" data-page-link="manage">Управление</a>
    </nav>

    <section class="fan-panel auth-panel" aria-label="Доступ к управлению">
      <details id="auth-disclosure">
        <summary>Вход для управления</summary>
        <p id="auth-help" class="fan-help">Пароль sudo пользователя admin на Raspberry Pi. Один вход для вентилятора, VPN и перезагрузки. Сессия действует 10 минут.</p>
        <form id="auth-form" novalidate aria-describedby="auth-help auth-feedback">
          <input type="hidden" name="username" value="admin" autocomplete="username">
          <label for="auth-password">Пароль sudo · admin</label>
          <div class="fan-auth"><input id="auth-password" name="password" type="password" autocomplete="current-password" maxlength="512" aria-describedby="auth-help auth-feedback" required><button type="button" data-toggle-secret="auth-password" aria-controls="auth-password" aria-pressed="false">Показать</button><button id="auth-login" class="fan-primary" type="submit">Войти</button></div>
        </form>
        <button id="auth-logout" type="button" hidden>Выйти из управления</button>
      </details>
      <p id="auth-feedback" class="fan-notice" role="status" aria-live="polite">Проверяем доступ…</p>
    </section>

    <div id="server-view">

    <section class="hero" aria-labelledby="hero-title">
      <div class="hero-copy">
        <p class="eyebrow">NODE 389102 · RU</p>
        <h1 id="hero-title">Сервер<br><em id="state-word">на связи.</em></h1>
        <p class="uptime-label">Непрерывная работа</p>
        <p class="uptime" id="uptime">—</p>
      </div>
      <div class="thermal" aria-label="Температура процессора">
        <div class="thermal-head">
          <span>Термоконтур</span>
          <span id="temp-state">норма</span>
        </div>
        <strong><span id="temperature">—</span><small>°C</small></strong>
        <progress id="temperature-bar" max="100" value="0">0%</progress>
        <div class="scale" aria-hidden="true"><span>20</span><span>50</span><span>80</span><span>100</span></div>
        <p>CPU package · <span id="throttle">без ограничений</span></p>
      </div>
    </section>

    <section class="telemetry" aria-label="Основные показатели">
      <article class="metric">
        <div class="metric-index" aria-hidden="true">01</div>
        <div class="metric-body">
          <div class="metric-line"><h2>Процессор</h2><strong id="cpu">—</strong></div>
          <progress id="cpu-bar" max="100" value="0">0%</progress>
          <p><span id="cores">—</span> ядер · <span id="frequency">—</span> ГГц</p>
        </div>
      </article>
      <article class="metric">
        <div class="metric-index" aria-hidden="true">02</div>
        <div class="metric-body">
          <div class="metric-line"><h2>Память</h2><strong id="memory">—</strong></div>
          <progress id="memory-bar" max="100" value="0">0%</progress>
          <p><span id="memory-detail">—</span> · swap <span id="swap">—</span></p>
        </div>
      </article>
      <article class="metric">
        <div class="metric-index" aria-hidden="true">03</div>
        <div class="metric-body">
          <div class="metric-line"><h2>Хранилище</h2><strong id="disk">—</strong></div>
          <progress id="disk-bar" max="100" value="0">0%</progress>
          <p><span id="disk-detail">—</span> свободно на системном диске</p>
        </div>
      </article>
    </section>

    <section class="core-panel" aria-labelledby="core-title" aria-describedby="core-help">
      <div class="core-heading">
        <div><p class="eyebrow">Процессор · по ядрам</p><h2 id="core-title">Распределение нагрузки</h2></div>
        <p id="core-summary">Ожидание измерений</p>
      </div>
      <p id="core-help">Текущая загрузка и последние 2 минуты после открытия страницы · обновление каждые 3 сек</p>
      <div id="core-grid" class="core-grid"></div>
      <p id="core-status" class="core-status" role="status" aria-live="polite">Загружаем показатели ядер…</p>
      <template id="core-template">
        <article class="core-metric">
          <div class="core-label"><h3></h3><span class="core-id"></span></div>
          <div class="core-current"><strong>—</strong><span class="core-condition">Сбор данных</span></div>
          <progress max="100" value="0"></progress>
          <svg viewBox="0 0 240 72" role="img" preserveAspectRatio="none">
            <line x1="0" y1="6" x2="240" y2="6" class="core-guide"></line>
            <line x1="0" y1="36" x2="240" y2="36" class="core-guide"></line>
            <line x1="0" y1="66" x2="240" y2="66" class="core-guide"></line>
            <path class="core-trace"></path>
            <circle class="core-dot" r="3"></circle>
          </svg>
          <div class="core-scale" aria-hidden="true"><span>−2 мин</span><span>сейчас</span></div>
        </article>
      </template>
    </section>

    <section class="vpn-sheet project-panel" aria-labelledby="projects-title">
      <div class="fan-heading"><h2 id="projects-title">Проекты</h2><span data-project-summary>Проверяем приложения…</span></div>
      <p class="fan-help">Приложения на сервере · контейнеры, HTTP и запуск после перезагрузки</p>
      <div data-project-list class="project-list"><p class="fan-help">Загружаем состояние проектов…</p></div>
      <p data-project-updated class="fan-help">Обновление каждые 15 секунд</p>
    </section>

    <section class="trace-panel" aria-labelledby="trace-title">
      <div class="trace-copy">
        <p class="eyebrow">Последние 2 минуты</p>
        <h2 id="trace-title">Пульс нагрузки</h2>
        <p>Средняя загрузка: <span id="load">—</span></p>
      </div>
      <svg id="trace" viewBox="0 0 640 120" role="img" aria-label="История загрузки процессора">
        <line x1="0" y1="30" x2="640" y2="30" class="trace-grid"></line>
        <line x1="0" y1="60" x2="640" y2="60" class="trace-grid"></line>
        <line x1="0" y1="90" x2="640" y2="90" class="trace-grid"></line>
        <polyline id="trace-fill" points="" class="trace-fill"></polyline>
        <polyline id="trace-line" points="" class="trace-line"></polyline>
      </svg>
    </section>

    <section class="facts" aria-label="Дополнительные показатели">
      <dl>
        <div><dt>Контейнеры</dt><dd><strong id="containers">—</strong><span id="container-health">—</span></dd></div>
        <div><dt>Сеть ↓</dt><dd><strong id="network-down">—</strong><span>приём</span></dd></div>
        <div><dt>Сеть ↑</dt><dd><strong id="network-up">—</strong><span>передача</span></dd></div>
        <div><dt>Система</dt><dd><strong id="host">webserver</strong><span id="platform">Debian · ARM64</span></dd></div>
      </dl>
    </section>

    <section class="machine" aria-labelledby="machine-title">
      <div class="machine-copy">
        <p class="eyebrow">Дополнительная информация</p>
        <h2 id="machine-title">Паспорт узла</h2>
        <p>Техническая конфигурация без публикации сетевых адресов, серийных номеров и состава процессов.</p>
      </div>
      <dl class="machine-grid">
        <div><dt>Платформа</dt><dd><strong id="machine-model">—</strong><span id="machine-arch">—</span></dd></div>
        <div><dt>Процессор</dt><dd><strong id="cpu-model">—</strong><span id="cpu-spec">—</span></dd></div>
        <div><dt>Операционная система</dt><dd><strong id="os-detail">—</strong><span>стабильная ветка</span></dd></div>
        <div><dt>Ядро</dt><dd><strong id="kernel">—</strong><span>Linux</span></dd></div>
        <div><dt>Последняя загрузка</dt><dd><strong id="boot-time">—</strong><span id="server-timezone">—</span></dd></div>
        <div><dt>Оперативная память</dt><dd><strong id="ram-total">—</strong><span id="swap-total">—</span></dd></div>
        <div><dt>Системный диск</dt><dd><strong id="disk-total">—</strong><span id="disk-free">—</span></dd></div>
        <div><dt>Контейнерный runtime</dt><dd><strong id="docker-engine">—</strong><span id="docker-state">—</span></dd></div>
        <div><dt>Systemd</dt><dd><strong id="systemd">—</strong><span id="systemd-detail">—</span></dd></div>
        <div><dt>Публичный доступ</dt><dd><strong>api.pi-hardbox.ru</strong><span>Tuna · RU · верифицирован · ID 389102</span></dd></div>
      </dl>
    </section>

    </div>


    <div id="manage-view">
      <section class="vpn-hero" aria-labelledby="manage-title">
        <div><p class="eyebrow">Обслуживание узла</p><h1 id="manage-title">Управление сервером</h1><p>Охлаждение, автозапуск и перезагрузка Raspberry Pi.</p></div>
        <div class="vpn-signal"><strong id="manage-state">Проверяем…</strong><span>Изменения доступны после входа</span></div>
      </section>
    <section class="fan-panel" aria-labelledby="fan-title">
      <div class="fan-heading">
        <div><p class="eyebrow">Охлаждение</p><h2 id="fan-title">Вентилятор</h2></div>
        <p class="fan-reading"><strong id="fan-rpm">—</strong> <span>об/мин</span></p>
      </div>
      <div class="fan-summary"><span id="fan-mode">Подключение к контроллеру</span><strong id="fan-pwm">—</strong></div>
      <p id="fan-safety" class="fan-notice" role="status" aria-live="polite"></p>
      <details id="fan-controls">
        <summary>Управлять охлаждением</summary>
        <p class="fan-help" id="fan-help">В ручном режиме скорость действует 30 минут, затем возвращается автоматика. При 75 °C защита включает 100%.</p>
        <form id="fan-form" novalidate aria-describedby="fan-help fan-feedback">
<p class="fan-help">Изменение режима доступно после входа с паролем sudo.</p>
          <fieldset id="fan-settings" disabled>
            <legend>Режим работы</legend>
            <div class="fan-modes"><label><input type="radio" name="fan-mode" value="auto" checked> Автоматически</label><label><input type="radio" name="fan-mode" value="manual"> Вручную</label></div>
            <div id="fan-speed-control" hidden>
              <label for="fan-speed">Скорость <output id="fan-speed-value" for="fan-speed">50%</output></label>
              <input id="fan-speed" type="range" min="20" max="100" step="1" value="50" aria-describedby="fan-speed-hint">
              <p id="fan-speed-hint" class="fan-help">20–100% мощности. Фактические обороты показаны выше.</p>
            </div>
            <button id="fan-apply" class="fan-primary" type="submit">Применить режим</button>
          </fieldset>
          <p id="fan-feedback" class="fan-notice" role="status" aria-live="polite"></p>
        </form>
      </details>
    </section>


      <section class="vpn-sheet" aria-labelledby="startup-title">
        <div class="fan-heading"><h2 id="startup-title">Автозапуск</h2><span id="startup-state">Ожидание данных</span></div>
        <p class="fan-help">Службы запускаются после включения платы. VPN не зависит от открытой вкладки сайта.</p>
        <dl id="startup-services" class="vpn-grid"></dl>
      </section>
      <section class="vpn-sheet project-panel" aria-labelledby="manage-projects-title">
        <div class="fan-heading"><h2 id="manage-projects-title">Проекты</h2><span data-project-summary>Проверяем приложения…</span></div>
        <p class="fan-help">Работа приложений и автозапуск их контейнеров</p>
        <div data-project-list class="project-list"><p class="fan-help">Загружаем состояние проектов…</p></div>
        <p data-project-updated class="fan-help">Обновление каждые 15 секунд</p>
      </section>
      <section class="fan-panel reboot-panel" aria-labelledby="reboot-title">
        <h2 id="reboot-title">Перезагрузка сервера</h2>
        <p id="reboot-help" class="fan-help">Сайт, Wi-Fi и контейнеры временно отключатся. После загрузки службы запустятся автоматически.</p>
        <button id="reboot-open" type="button" disabled aria-describedby="reboot-help reboot-feedback">Перезагрузить сервер…</button>
        <p id="reboot-feedback" class="fan-notice" role="status" aria-live="polite"></p>
      </section>
    </div>
    <dialog id="reboot-dialog" aria-labelledby="reboot-confirm-title" aria-describedby="reboot-confirm-help">
      <h2 id="reboot-confirm-title">Перезагрузить Raspberry Pi?</h2>
      <p id="reboot-confirm-help">Сайт, Wi-Fi и все контейнеры временно отключатся. Перезагрузка начнётся через 8 секунд после подтверждения.</p>
      <div class="dialog-actions"><button id="reboot-cancel" type="button" autofocus>Отмена</button><button id="reboot-confirm" class="danger-action" type="button">Перезагрузить</button></div>
    </dialog>

    <div id="vpn-view">
      <section class="vpn-hero" aria-labelledby="vpn-title">
        <div>
          <p class="eyebrow">Домашняя сеть · отдельный маршрут</p>
          <h1 id="vpn-title">VPN и Wi-Fi</h1>
          <p>Российские сервисы — напрямую, остальные сайты — через VPN.<br>Сервер и контейнеры работают напрямую.</p>
        </div>
        <div class="vpn-signal" role="status" aria-live="polite">
          <span class="eyebrow">Проверка соединения</span>
          <strong id="vpn-state">Проверяем…</strong>
          <span id="vpn-check-time">Ожидание данных</span>
        </div>
      </section>

      <section class="facts vpn-facts" aria-label="Показатели Wi-Fi и VPN">
        <dl>
          <div><dt>Устройства</dt><dd><strong id="vpn-clients">—</strong><span>подключено к Wi-Fi</span></dd></div>
          <div><dt>К устройствам ↓</dt><dd><strong id="vpn-down">—</strong><span>передача через точку доступа</span></dd></div>
          <div><dt>От устройств ↑</dt><dd><strong id="vpn-up">—</strong><span>приём от устройств</span></dd></div>
          <div><dt>Проверка HTTPS</dt><dd><strong id="vpn-latency">—</strong><span>время запроса через туннель</span></dd></div>
        </dl>
      </section>

      <section class="vpn-sheet" aria-labelledby="wifi-title">
        <div class="fan-heading"><h2 id="wifi-title">Наша Wi-Fi-сеть</h2><span id="vpn-wifi-state">Ожидание данных</span></div>
        <dl class="vpn-grid">
          <div><dt>Название сети</dt><dd id="vpn-ssid">—</dd></div>
          <div><dt>Диапазон и канал</dt><dd id="vpn-band">—</dd></div>
          <div><dt>Защита Wi-Fi</dt><dd id="vpn-security">—</dd></div>
          <div><dt>Подключение VPN</dt><dd>VLESS · REALITY · TCP</dd></div>
          <div><dt>VPN-служба</dt><dd id="vpn-service">—</dd></div>
          <div><dt>Контроль маршрутизации Wi-Fi</dt><dd id="vpn-protection">—</dd></div>
          <div class="vpn-wide"><dt>Последнее изменение ключа</dt><dd id="vpn-key-time">—</dd></div>
        </dl>
        <p class="fan-help">Соединения для VPN не переключаются на прямой выход при сбое. Если остановлена сама VPN-служба, весь интернет Wi-Fi недоступен. Обычный интернет сервера продолжает работать.</p>
        <p id="vpn-status-note" class="fan-notice" role="status" aria-live="polite">Получаем данные контроллера…</p>
      </section>

      <section class="vpn-sheet" aria-labelledby="routing-title">
        <div class="fan-heading"><h2 id="routing-title">Куда идёт трафик</h2><span id="routing-mode">Ожидание правил</span></div>
        <p class="fan-help" id="routing-groups">Загружаем список российских сервисов…</p>
        <dl class="vpn-grid">
          <div><dt>Напрямую · получено / отправлено</dt><dd id="routing-direct-traffic">—</dd></div>
          <div><dt>Через VPN · получено / отправлено</dt><dd id="routing-vpn-traffic">—</dd></div>
          <div><dt>Базовый список исключений</dt><dd id="routing-base">—</dd></div>
          <div><dt>Ваши исключения · напрямую / VPN</dt><dd id="routing-custom">—</dd></div>
          <div><dt>Версия списка установлена</dt><dd id="routing-updated">—</dd></div>
          <div><dt>Последняя проверка обновлений</dt><dd id="routing-checked">—</dd></div>
        </dl>
        <p class="fan-help">Счётчики с запуска VPN-службы, включая DNS и проверку туннеля; после перезапуска обнуляются. История посещений не сохраняется. Если домен скрыт или неизвестен, соединение остаётся в VPN. DNS-запросы идут через VPN.</p>
        <p class="fan-help">Списки российских сервисов проверяются ежедневно. Источник: <a href="https://github.com/v2fly/domain-list-community" rel="noreferrer" target="_blank">v2fly/domain-list-community</a>. Все сайты в зоне .ru автоматически в исключения не добавляются.</p>
      </section>

      <section class="fan-panel vpn-editor" aria-labelledby="vpn-editor-title">
        <details id="vpn-controls">
          <summary id="vpn-editor-title">Управление VPN и исключениями</summary>
          <p class="fan-help" id="vpn-editor-help">Войдите выше с паролем sudo пользователя admin. Изменения применяются с проверкой и возвратом прежних настроек при ошибке. Возможен короткий перерыв соединения.</p>
          <form id="routing-form" novalidate aria-describedby="routing-help vpn-feedback">
            <h3>Мои исключения</h3>
            <p id="routing-help" class="fan-help">Один домен на строку, без https:// и пути. Правило действует и для поддоменов. Ваш список «Через VPN» имеет приоритет над прямым выходом и базовыми исключениями. До 50 доменов в каждом списке.</p>
            <div class="form-actions routing-tools">
              <button id="routing-load" type="button" disabled>Загрузить мои исключения</button>
              <button id="routing-refresh" type="button" disabled>Проверить обновления базового списка</button>
            </div>
            <fieldset id="routing-settings" disabled>
              <legend>Дополнения к базовому списку</legend>
              <div class="routing-inputs">
                <div><label for="routing-direct">Напрямую</label><textarea id="routing-direct" rows="6" maxlength="6000" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="example.ru" aria-describedby="routing-help vpn-feedback"></textarea></div>
                <div><label for="routing-vpn">Через VPN</label><textarea id="routing-vpn" rows="6" maxlength="6000" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="example.com" aria-describedby="routing-help vpn-feedback"></textarea></div>
              </div>
              <div class="form-actions"><button id="routing-apply" class="fan-primary" type="submit">Сохранить исключения</button></div>
            </fieldset>
            <p id="routing-draft" class="fan-help">Ваши списки доступны после входа и загрузки.</p>
          </form>
          <form id="vpn-form" novalidate aria-describedby="vpn-editor-help vpn-feedback">
            <h3>Обновить ключ VPN</h3>
            <fieldset id="vpn-settings" disabled>
              <legend>Новый ключ подключения</legend>
              <label for="vpn-uri">Полная ссылка VLESS</label>
              <div class="fan-auth"><input id="vpn-uri" type="password" autocomplete="off" spellcheck="false" maxlength="8192" placeholder="vless://…" aria-describedby="vpn-uri-help vpn-feedback" required><button type="button" data-toggle-secret="vpn-uri" aria-controls="vpn-uri" aria-pressed="false">Показать</button></div>
              <p class="fan-help" id="vpn-uri-help">Поддерживается VLESS + REALITY + TCP. Ключ не отображается в статусе и не сохраняется в браузере.</p>
              <button id="vpn-apply" class="fan-primary" type="submit">Проверить и применить</button>
            </fieldset>
          </form>
          <p id="vpn-feedback" class="fan-notice" role="status" aria-live="polite"></p>
        </details>
      </section>
    </div>

    <footer>
      <span>api.pi-hardbox.ru</span>
      <span id="updated" aria-live="polite">Ожидание телеметрии</span>
    </footer>
  </main>
  <script src="/app.js?v=img2txt1" defer></script>
</body>
</html>
'''


CSS = r''':root {
  --ink: #16211d;
  --ink-soft: #32423a;
  --paper: #e7ece5;
  --paper-bright: #f4f6f1;
  --rule: #a9b6ab;
  --raspberry: #b62d4d;
  --raspberry-dark: #82223a;
  --leaf: #4d7665;
  --amber: #a96f24;
  --muted: #657169;
  --focus: #176c94;
  --radius-sm: .4rem;
  --radius-md: .8rem;
  --control-height: 44px;
  --control-gap: 8px;
}

* { box-sizing: border-box; scrollbar-color: var(--ink-soft) var(--paper); scrollbar-width: thin; }

#server-view, #vpn-view, #manage-view { display: none; }
body[data-page=server] #server-view, body[data-page=vpn] #vpn-view, body[data-page=manage] #manage-view { display: block; }
body[data-page=server] .auth-panel { display: none; }
.section-tabs { display: flex; gap: 1.5rem; margin: 8px 0 18px; border-bottom: 1px solid var(--rule); }
.section-tabs a { display: block; padding: .8rem .15rem; min-height: 44px; border-bottom: 3px solid transparent; text-decoration: none; font-weight: 700; font-size: .9rem; }
.section-tabs a:hover { color: var(--raspberry-dark); }
body[data-page=server] [data-page-link=server], body[data-page=vpn] [data-page-link=vpn], body[data-page=manage] [data-page-link=manage] { border-bottom-color: var(--raspberry); color: var(--raspberry-dark); }
.vpn-hero { display: grid; grid-template-columns: 1.4fr 1fr; gap: 2rem; align-items: center; padding: 38px; border-radius: var(--radius-md); background: var(--ink); color: var(--paper-bright); }
.vpn-hero .eyebrow { color: var(--paper); }
.vpn-hero h1 { margin: .7rem 0 1rem; font-size: clamp(2.4rem, 5vw, 4.5rem); line-height: 1; }
.vpn-hero p:last-child { font-size: .9rem; line-height: 1.6; color: var(--paper); }
.vpn-signal { display: grid; gap: .7rem; padding-left: 28px; border-left: 1px solid var(--leaf); }
.vpn-signal strong { font: 500 clamp(1.5rem, 3vw, 2.4rem)/1.2 "DejaVu Sans Mono", monospace; }
.vpn-signal > span:last-child { font-size: .75rem; color: var(--paper); }
.vpn-sheet { margin-top: 14px; padding: 24px 28px; border: 1px solid var(--rule); border-radius: var(--radius-md); background: var(--paper-bright); }
.vpn-sheet h2 { margin: 0; font-size: 1.4rem; }
.vpn-sheet .fan-heading > span { font-size: .8rem; }
.vpn-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); margin: 1.4rem 0 1rem; }
.vpn-grid > div { min-width: 0; padding: 16px 0; border-top: 1px solid var(--rule); }
.vpn-grid > div:nth-child(even) { padding-left: 24px; }
.vpn-grid dt { color: var(--ink-soft); font-size: .75rem; margin-bottom: .5rem; }
.vpn-grid dd { margin: 0; font: 600 .95rem/1.45 "DejaVu Sans Mono", monospace; overflow-wrap: anywhere; }
.vpn-wide { grid-column: 1 / -1; }
.status-light { --signal: var(--muted); display: inline-flex; align-items: center; gap: 10px; min-height: 24px; color: var(--ink); font: 600 .85rem/1.5 "DejaVu Sans Mono", monospace; }
.status-light::before { content: ""; flex: 0 0 10px; width: 10px; height: 10px; border-radius: 50%; background: var(--signal); box-shadow: 0 0 0 3px color-mix(in srgb, var(--signal) 16%, transparent), inset 0 1px 1px rgb(255 255 255 / .5); }
.status-light[data-state=on] { --signal: var(--leaf); }
.status-light[data-state=warn] { --signal: var(--amber); }
.status-light[data-state=off] { --signal: var(--raspberry); }
.service-statuses { display: flex; gap: 16px 28px; flex-wrap: wrap; }
.state-reading { display: grid; gap: 5px; min-width: 0; }
.state-reading > small { font: 400 .7rem/1.4 "Trebuchet MS", "DejaVu Sans", sans-serif; color: var(--ink-soft); }
.project-list { margin-top: 20px; min-height: 146px; }
.project-item { padding: 20px 0 8px; border-top: 1px solid var(--rule); }
.project-heading { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 10px; }
.project-heading h3 { margin: 0; font: 700 1.15rem/1.4 "DejaVu Sans Mono", monospace; overflow-wrap: anywhere; }
.project-readings { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 16px; margin: 20px 0 16px; }
.project-readings dt { margin-bottom: 6px; font-size: .75rem; color: var(--ink-soft); }
.project-readings dd { margin: 0; }
.project-detail { margin: 0; font-size: .8rem; line-height: 1.6; color: var(--ink-soft); }
@media (max-width: 620px) { .project-readings { grid-template-columns: 1fr; gap: 12px; } .project-readings > div { display: grid; grid-template-columns: 6rem 1fr; gap: 12px; align-items: baseline; } .project-readings dt { margin: 0; } .service-statuses { gap: 14px 24px; } }
.fan-panel.vpn-editor details { border-top: 0; padding-top: 0; margin-top: 0; }
#vpn-feedback { min-height: 3em; }
.fan-panel.vpn-editor form { max-width: 52rem; }
.fan-panel.auth-panel { margin: 0 0 18px; }
.fan-panel.auth-panel details { border-top: 0; margin-top: 0; padding-top: 0; }
.auth-panel .fan-notice { margin-bottom: 0; }
.reboot-panel { margin-top: 14px; }
.reboot-panel h2 { margin-top: 0; }
dialog { max-width: min(32rem, calc(100vw - 40px)); max-height: calc(100dvh - 40px); overflow: auto; box-sizing: border-box; padding: 28px; border: 1px solid var(--rule); border-radius: var(--radius-md); background: var(--paper-bright); color: var(--ink); }
dialog::backdrop { background: rgb(21 34 28 / .65); }
dialog h2 { margin-top: 0; }
dialog p { line-height: 1.6; }
.dialog-actions { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: var(--control-gap); margin-top: 24px; }
dialog .danger-action { background: var(--raspberry); color: var(--paper-bright); border-color: var(--raspberry); }
dialog .danger-action:hover { background: var(--raspberry-dark); }
.vpn-editor h3 { font-size: 1.1rem; margin: 1.7rem 0 1rem; }
.routing-inputs { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 18px; margin-bottom: 14px; }
.routing-inputs > div { min-width: 0; }
.routing-inputs textarea { display: block; width: 100%; box-sizing: border-box; resize: none; field-sizing: content; min-height: 11rem; max-height: 22rem; margin-top: var(--control-gap); padding: 12px; border: 1px solid var(--rule); border-radius: var(--radius-sm); background: var(--paper-bright); color: var(--ink); font: .85rem/1.6 "DejaVu Sans Mono", monospace; }
.routing-inputs textarea:focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
.routing-inputs textarea[aria-invalid=true] { border-color: var(--raspberry); }
#routing-form { padding-bottom: 24px; border-bottom: 1px solid var(--rule); }
@media (max-width: 780px) { .vpn-hero { grid-template-columns: 1fr; gap: 1rem; padding: 26px; } .vpn-signal { border-left: 0; border-top: 1px solid var(--leaf); padding: 20px 0 0; } }
@media (max-width: 460px) { .vpn-sheet { padding: 20px; } .vpn-grid, .routing-inputs { grid-template-columns: 1fr; } .vpn-grid > div:nth-child(even) { padding-left: 0; } }

html {
  min-width: 20rem;
  background: var(--paper);
  scrollbar-color: var(--ink-soft) var(--paper);
  scrollbar-width: thin;
}

*::-webkit-scrollbar { width: 10px; height: 10px; }
*::-webkit-scrollbar-track { background: var(--paper); }
*::-webkit-scrollbar-thumb { background: var(--ink-soft); border: 3px solid var(--paper); border-radius: 99px; }

body {
  margin: 0;
  color: var(--ink);
  background:
    linear-gradient(rgba(22, 33, 29, .045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(22, 33, 29, .045) 1px, transparent 1px),
    var(--paper);
  background-size: 32px 32px;
  font-family: "Trebuchet MS", "DejaVu Sans", sans-serif;
  font-size: 16px;
  line-height: 1.45;
}

a { color: inherit; }
a:focus-visible { outline: 3px solid var(--focus); outline-offset: 5px; }

.shell {
  width: min(1120px, calc(100% - 32px));
  margin: 0 auto;
  padding: 26px 0 34px;
}

.masthead,
footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}

.masthead {
  min-height: 42px;
  margin-bottom: 14px;
}

.brand {
  display: inline-flex;
  align-items: center;
  gap: .65rem;
  text-decoration: none;
  font: 800 .84rem/1 "DejaVu Sans Mono", "Liberation Mono", monospace;
  letter-spacing: .12em;
}

.brand > span:last-child > span { color: var(--raspberry); }

.brand-mark {
  display: grid;
  width: 2.2rem;
  aspect-ratio: 1;
  place-items: center;
  border-radius: 50%;
  color: var(--paper-bright);
  background: var(--raspberry);
  letter-spacing: -.08em;
}

.live {
  display: flex;
  align-items: center;
  gap: .55rem;
  color: var(--ink-soft);
  font: 700 .71rem/1 "DejaVu Sans Mono", "Liberation Mono", monospace;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.live-dot {
  width: .58rem;
  height: .58rem;
  border: 2px solid var(--paper);
  border-radius: 50%;
  background: var(--amber);
  box-shadow: 0 0 0 1px var(--amber);
}

body[data-state="online"] .live-dot { background: var(--leaf); box-shadow: 0 0 0 1px var(--leaf); }
body[data-state="offline"] .live-dot { background: var(--raspberry); box-shadow: 0 0 0 1px var(--raspberry); }
.live-rate { color: var(--muted); }

.hero {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(270px, .7fr);
  min-height: 360px;
  overflow: hidden;
  border-radius: var(--radius-md);
  color: var(--paper-bright);
  background: var(--ink);
  box-shadow: 0 16px 45px rgba(22, 33, 29, .16);
}

.hero-copy {
  display: flex;
  flex-direction: column;
  padding: clamp(28px, 5vw, 58px);
  background:
    radial-gradient(circle at 78% 22%, rgba(182, 45, 77, .22), transparent 32%),
    linear-gradient(135deg, rgba(255, 255, 255, .025), transparent 55%);
}

.eyebrow {
  margin: 0 0 1.4rem;
  color: #b9c5bc;
  font: 700 .7rem/1.3 "DejaVu Sans Mono", "Liberation Mono", monospace;
  letter-spacing: .13em;
  text-transform: uppercase;
}

h1 {
  margin: 0;
  font-family: Georgia, "DejaVu Serif", serif;
  font-size: clamp(3.15rem, 8vw, 6.7rem);
  font-weight: 400;
  letter-spacing: -.065em;
  line-height: .84;
}

h1 em { color: #e86d83; font-weight: 400; }

.uptime-label {
  margin: auto 0 4px;
  color: #aebbb2;
  font: 700 .68rem/1 "DejaVu Sans Mono", "Liberation Mono", monospace;
  letter-spacing: .12em;
  text-transform: uppercase;
}

.uptime {
  min-height: 2rem;
  margin: 0;
  font: 500 clamp(1.2rem, 2.8vw, 1.85rem)/1.2 "DejaVu Sans Mono", "Liberation Mono", monospace;
  font-variant-numeric: tabular-nums;
}

.thermal {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: clamp(26px, 4vw, 46px);
  color: var(--ink);
  background: #cbd5cb;
  border-left: 1px solid rgba(255, 255, 255, .12);
}

.thermal-head,
.metric-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
}

.thermal-head {
  font: 700 .68rem/1 "DejaVu Sans Mono", "Liberation Mono", monospace;
  letter-spacing: .08em;
  text-transform: uppercase;
}

.thermal > strong {
  margin: 1.7rem 0 1.2rem;
  font: 500 clamp(4.1rem, 8vw, 7.4rem)/.85 "DejaVu Sans Mono", "Liberation Mono", monospace;
  letter-spacing: -.09em;
  font-variant-numeric: tabular-nums;
}

.thermal > strong small { margin-left: .15em; font-size: .24em; letter-spacing: 0; }
.thermal > p { margin: 1rem 0 0; color: var(--ink-soft); font-size: .78rem; }

progress {
  width: 100%;
  height: 7px;
  overflow: hidden;
  border: 0;
  border-radius: 99px;
  color: var(--raspberry);
  background: rgba(22, 33, 29, .13);
  accent-color: var(--raspberry);
}

progress::-webkit-progress-bar { background: rgba(22, 33, 29, .13); border-radius: 99px; }
progress::-webkit-progress-value { background: var(--raspberry); border-radius: 99px; transition: width .45s ease; }
progress::-moz-progress-bar { background: var(--raspberry); border-radius: 99px; transition: width .45s ease; }
.metric progress { color: var(--leaf); accent-color: var(--leaf); }
.metric progress::-webkit-progress-value { background: var(--leaf); }
.metric progress::-moz-progress-bar { background: var(--leaf); }

.scale { display: flex; justify-content: space-between; margin-top: .5rem; color: var(--muted); font: 600 .58rem/1 monospace; }

.telemetry {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin-top: 14px;
  border: 1px solid var(--rule);
  border-radius: var(--radius-md);
  background: rgba(244, 246, 241, .78);
}

.metric { display: grid; grid-template-columns: 42px 1fr; min-width: 0; padding: 24px 24px 22px 17px; }
.metric + .metric { border-left: 1px solid var(--rule); }
.metric-index { color: var(--raspberry); font: 700 .63rem/1 "DejaVu Sans Mono", monospace; }
.metric h2 { margin: 0; font-size: .85rem; font-weight: 700; }
.metric strong { font: 600 1.55rem/1 "DejaVu Sans Mono", monospace; letter-spacing: -.06em; font-variant-numeric: tabular-nums; }
.metric progress { margin: 1.25rem 0 .75rem; height: 5px; }
.metric p { min-height: 2.4em; margin: 0; color: var(--muted); font-size: .73rem; }

.trace-panel {
  display: grid;
  grid-template-columns: minmax(180px, .45fr) minmax(0, 1.4fr);
  align-items: end;
  gap: 36px;
  min-height: 185px;
  margin-top: 14px;
  padding: 27px 30px;
  border-radius: var(--radius-md);
  color: var(--paper-bright);
  background: #293831;
}

.trace-copy .eyebrow { margin-bottom: .6rem; }
.trace-copy h2 { margin: 0; font: 400 1.65rem/1.1 Georgia, serif; }
.trace-copy > p:last-child { margin: .7rem 0 0; color: #b8c4bb; font-size: .75rem; }
#trace { display: block; width: 100%; height: 120px; overflow: visible; }
.trace-grid { stroke: rgba(231, 236, 229, .12); stroke-width: 1; stroke-dasharray: 3 7; }
.trace-line { fill: none; stroke: #ee8296; stroke-width: 3; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.trace-fill { fill: none; stroke: rgba(238, 130, 150, .15); stroke-width: 15; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }

.facts { margin-top: 14px; border-top: 3px solid var(--ink); border-bottom: 1px solid var(--ink); }
.facts dl { display: grid; grid-template-columns: repeat(4, 1fr); margin: 0; }
.facts dl > div { padding: 18px 22px 20px; }
.facts dl > div + div { border-left: 1px solid var(--rule); }
.facts dt { margin-bottom: .7rem; color: var(--muted); font: 700 .65rem/1 monospace; letter-spacing: .08em; text-transform: uppercase; }
.facts dd { margin: 0; }
.facts strong, .facts span { display: block; }
.facts strong { overflow: hidden; font: 600 1.05rem/1.2 "DejaVu Sans Mono", monospace; text-overflow: ellipsis; white-space: nowrap; }
.facts span { margin-top: .3rem; color: var(--muted); font-size: .68rem; }

.machine {
  display: grid;
  grid-template-columns: minmax(190px, .48fr) minmax(0, 1.52fr);
  gap: clamp(28px, 5vw, 64px);
  margin-top: 14px;
  padding: clamp(26px, 4vw, 42px);
  border: 1px solid var(--rule);
  border-radius: var(--radius-md);
  background: rgba(244, 246, 241, .82);
}

.machine-copy .eyebrow { margin-bottom: .65rem; color: var(--raspberry-dark); }
.machine-copy h2 { margin: 0; font: 400 clamp(1.9rem, 3.4vw, 2.7rem)/1 Georgia, "DejaVu Serif", serif; letter-spacing: -.035em; }
.machine-copy > p:last-child { max-width: 28rem; margin: 1rem 0 0; color: var(--muted); font-size: .78rem; }
.machine-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); margin: 0; border-top: 1px solid var(--rule); border-left: 1px solid var(--rule); }
.machine-grid > div { min-width: 0; padding: 15px 17px 16px; border-right: 1px solid var(--rule); border-bottom: 1px solid var(--rule); }
.machine-grid dt { margin-bottom: .55rem; color: var(--muted); font: 700 .61rem/1.2 "DejaVu Sans Mono", monospace; letter-spacing: .08em; text-transform: uppercase; }
.machine-grid dd { min-height: 2.65rem; margin: 0; }
.machine-grid strong,
.machine-grid span { display: block; overflow-wrap: anywhere; }
.machine-grid strong { font: 600 .83rem/1.3 "DejaVu Sans Mono", monospace; font-variant-numeric: tabular-nums; }
.machine-grid span { min-height: 1rem; margin-top: .28rem; color: var(--muted); font-size: .66rem; }

footer { padding: 18px 2px 0; color: var(--muted); font: 600 .64rem/1.3 "DejaVu Sans Mono", monospace; letter-spacing: .03em; }

@media (max-width: 780px) {
  .hero { grid-template-columns: 1fr; }
  .hero-copy { min-height: 330px; }
  .thermal { border-top: 1px solid rgba(255, 255, 255, .14); border-left: 0; }
  .telemetry { grid-template-columns: 1fr; }
  .metric + .metric { border-top: 1px solid var(--rule); border-left: 0; }
  .trace-panel { grid-template-columns: 1fr; gap: 18px; }
  .facts dl { grid-template-columns: repeat(2, 1fr); }
  .facts dl > div + div { border-left: 0; }
  .facts dl > div:nth-child(even) { border-left: 1px solid var(--rule); }
  .facts dl > div:nth-child(n+3) { border-top: 1px solid var(--rule); }
  .machine { grid-template-columns: 1fr; }
}

@media (max-width: 460px) {
  .shell { width: min(100% - 20px, 1120px); padding-top: 14px; }
  .live-rate { display: none; }
  .hero-copy { min-height: 305px; }
  h1 { font-size: clamp(3rem, 17vw, 4.4rem); }
  .metric { grid-template-columns: 32px 1fr; padding-right: 17px; }
  .trace-panel { padding: 24px 20px; }
  .machine { padding: 24px 20px; }
  .machine-grid { grid-template-columns: 1fr; }
  footer { align-items: flex-start; flex-direction: column; gap: .35rem; }
}

.core-panel { margin-top: 14px; border: 1px solid var(--rule); border-radius: var(--radius-md); background: var(--paper-bright); overflow: hidden; }
.core-heading { display: flex; align-items: end; justify-content: space-between; gap: 1rem; flex-wrap: wrap; padding: 24px 28px 0; }
.core-heading .eyebrow { margin: 0 0 .4rem; color: var(--raspberry); }
.core-heading h2 { margin: 0; font-size: 1.4rem; }
.core-heading > p { margin: 0; color: var(--ink-soft); font-size: .8rem; font-variant-numeric: tabular-nums; }
#core-help { margin: .6rem 28px 1.2rem; color: var(--ink-soft); font-size: .8rem; }
.core-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); min-height: 246px; border-top: 1px solid var(--rule); }
.core-metric { min-width: 0; min-height: 246px; padding: 20px 22px 16px; }
.core-metric + .core-metric { border-left: 1px solid var(--rule); }
.core-label, .core-current, .core-scale { display: flex; align-items: baseline; justify-content: space-between; gap: .5rem; }
.core-label h3 { margin: 0; font-size: .85rem; }
.core-id { color: var(--muted); font: .65rem/1 "DejaVu Sans Mono", monospace; }
.core-current { margin-top: 1rem; min-height: 2.2rem; }
.core-current strong { font: 600 1.8rem/1 "DejaVu Sans Mono", monospace; font-variant-numeric: tabular-nums; letter-spacing: -.07em; }
.core-condition { color: var(--ink-soft); font-size: .65rem; text-align: right; max-width: 6rem; }
.core-metric[data-busy=true] .core-condition { color: var(--raspberry-dark); font-weight: 700; }
.core-metric progress { margin: .6rem 0 1rem; height: 5px; }
.core-metric svg { display: block; width: 100%; height: 72px; overflow: visible; }
.core-guide { stroke: var(--rule); stroke-width: .6; stroke-dasharray: 2 6; }
.core-trace { fill: none; stroke: var(--raspberry); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; vector-effect: non-scaling-stroke; }
.core-dot { fill: var(--raspberry); }
.core-scale { margin-top: .45rem; color: var(--muted); font: .6rem/1 "DejaVu Sans Mono", monospace; }
.core-status { min-height: 1.5em; margin: .5rem 28px .75rem; color: var(--ink-soft); font-size: .75rem; }
body[data-state=offline] .core-grid { opacity: .65; }
@media (max-width: 900px) {
  .core-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); min-height: 492px; }
  .core-metric:nth-child(odd) { border-left: 0; }
  .core-metric:nth-child(n+3) { border-top: 1px solid var(--rule); }
}
@media (max-width: 460px) {
  .core-heading { padding: 20px 20px 0; }
  #core-help { margin-left: 20px; margin-right: 20px; }
  .core-metric { padding: 16px 12px 14px; }
  .core-current { flex-wrap: wrap; gap: .4rem; min-height: 3.3rem; }
  .core-current strong { font-size: 1.6rem; }
  .core-condition { max-width: none; text-align: left; }
  .core-status { margin-left: 20px; margin-right: 20px; }
}

.fan-panel { margin-top: 14px; padding: 24px 28px; border: 1px solid var(--rule); border-radius: var(--radius-md); background: var(--paper-bright); }
.fan-heading, .fan-summary, .fan-modes { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.fan-heading h2 { margin: 0; font-size: 1.4rem; }
.fan-heading .eyebrow { margin: 0 0 .35rem; }
.fan-reading { margin: 0; font-family: "DejaVu Sans Mono", "Liberation Mono", monospace; }
.fan-reading strong { font-size: 1.75rem; }
.fan-summary { margin-top: 1rem; font-size: .9rem; }
.fan-summary strong { font-family: "DejaVu Sans Mono", "Liberation Mono", monospace; }
.fan-panel details { border-top: 1px solid var(--rule); margin-top: .75rem; padding-top: .75rem; }
.fan-panel summary { cursor: pointer; font-weight: 700; padding: .4rem 0; }
.fan-panel summary:hover { color: var(--raspberry-dark); }
.fan-panel form { width: 100%; max-width: 52rem; }
.fan-panel label { display: block; font-size: .9rem; line-height: 1.5; }
.fan-panel fieldset { border: 0; margin: 1rem 0 0; padding: 0; min-width: 0; }
.fan-panel legend { font-weight: 700; padding: 0 0 .5rem; }
.fan-panel button, dialog button, .fan-panel input[type=password], .fan-panel input[type=text] { min-height: var(--control-height); margin: 0; border: 1px solid var(--rule); border-radius: var(--radius-sm); padding: .65rem 1rem; font: inherit; line-height: 1.35; color: var(--ink); background: var(--paper-bright); }
.fan-panel button, dialog button { display: inline-flex; align-items: center; justify-content: center; gap: .5rem; cursor: pointer; font-size: .9rem; font-weight: 700; text-align: center; overflow-wrap: anywhere; }
.fan-panel button:hover, dialog button:hover { border-color: var(--ink); background: var(--paper); }
.fan-panel button:active, dialog button:active { transform: translateY(1px); }
.fan-panel button:disabled, dialog button:disabled { opacity: .55; cursor: not-allowed; transform: none; }
.fan-panel .fan-primary { background: var(--ink); color: var(--paper-bright); min-width: 12rem; }
.fan-panel fieldset > .fan-primary { margin-top: 1rem; }
.fan-panel .fan-primary:hover { background: var(--ink-soft); }
.fan-panel :focus-visible, dialog :focus-visible { outline: 3px solid var(--focus); outline-offset: 3px; }
.fan-panel [aria-invalid=true] { border-color: var(--raspberry); }
.fan-panel input { accent-color: var(--raspberry); }
.fan-auth { display: grid; grid-template-columns: minmax(0,1fr) 6.5rem; align-items: stretch; margin-top: var(--control-gap); gap: var(--control-gap); }
.fan-auth input { min-width: 0; width: 100%; }
.auth-panel .fan-auth { grid-template-columns: minmax(0,1fr) 6.5rem 7rem; }
.auth-panel .fan-auth .fan-primary { min-width: 0; }
.form-actions { display: flex; align-items: stretch; flex-wrap: wrap; gap: var(--control-gap); margin-top: 16px; }
.routing-tools { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); margin: 16px 0 20px; }
.routing-tools button { min-height: 64px; }
#auth-logout { margin-top: 12px; }
.fan-modes { justify-content: flex-start; gap: .5rem 1.5rem; }
.fan-modes label { min-height: 44px; display: flex; align-items: center; cursor: pointer; gap: .35rem; }
.fan-panel input[type=radio], .fan-panel input[type=range] { cursor: pointer; }
.fan-panel input[type=range] { width: 100%; min-height: 44px; margin: 0; }
.fan-panel output { font-weight: 700; margin-left: .5rem; }
.fan-help { font-size: .8rem; color: var(--ink-soft); margin: .5rem 0 .8rem; }
.fan-notice { font-size: .85rem; min-height: 1.5em; margin: .5rem 0 0; color: var(--ink-soft); }
.fan-notice[data-error=true] { color: var(--raspberry-dark); }
.fan-panel [hidden] { display: none; }
@media (max-width: 620px) {
  .auth-panel .fan-auth { grid-template-columns: minmax(0,1fr) 6.5rem; }
  .auth-panel .fan-auth .fan-primary { grid-column: 1 / -1; }
  .routing-tools { grid-template-columns: 1fr; }
  .form-actions > button, .fan-panel fieldset > .fan-primary, #reboot-open, #auth-logout { width: 100%; }
  .dialog-actions { grid-template-columns: 1fr; }
}
@media (max-width: 460px) { .fan-panel { padding: 20px; } .fan-reading strong { font-size: 1.4rem; } .fan-heading { align-items: flex-start; } }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto; transition-duration: .01ms; animation-duration: .01ms; animation-iteration-count: 1; }
}

@media (forced-colors: active) {
  html { scrollbar-color: auto; }
  .live-dot { forced-color-adjust: none; }
  progress { forced-color-adjust: auto; }
}
'''


JS = r'''(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const history = [];
  let timer = null;
  let controller = null;
  let fanAvailable = false;
  let authenticated = false;
  let serverAvailable = false;
  let authBusy = false;
  let rebootBusy = false;
  let rebootPending = false;
  let authTimer = null;
  let authVersion = 0;
  let authMessageLocked = false;
  let fanBusy = false;
  const coreViews = new Map();
  const coreNumber = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  let lastCoreStamp = 0;

  const text = (id, value) => { byId(id).textContent = value; };
  const clamp = (value, min, max) => Math.min(max, Math.max(min, Number(value) || 0));
  const percent = (value) => `${Math.round(clamp(value, 0, 100))}%`;

  function bytes(value) {
    const n = Number(value) || 0;
    if (n < 1024) return `${n.toFixed(0)} Б`;
    const units = ["КиБ", "МиБ", "ГиБ", "ТиБ"];
    let v = n / 1024;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return `${v >= 10 ? v.toFixed(1) : v.toFixed(2)} ${units[i]}`;
  }

  function rate(value) { return `${bytes(value)}/с`; }

  const vpnPage = document.body.dataset.page === "vpn";
  const managePage = document.body.dataset.page === "manage";
  document.querySelector(`[data-page-link="${document.body.dataset.page}"]`).setAttribute("aria-current", "page");
  document.querySelectorAll("[data-toggle-secret]").forEach(button => {
    button.addEventListener("click", () => {
      const field = byId(button.dataset.toggleSecret);
      const visible = field.type === "password";
      field.type = visible ? "text" : "password";
      button.textContent = visible ? "Скрыть" : "Показать";
      button.setAttribute("aria-pressed", String(visible));
    });
  });
  let vpnAvailable = false;
  let vpnBusy = false;
  let vpnUpdating = false;
  let vpnTimer = null;
  let vpnAbort = null;
  let vpnSequence = 0;
  let vpnJobTimer = null;
  let vpnJobDeadline = 0;
  let vpnAction = "key";
  let rulesRevision = null;
  let rulesLoaded = false;
  let rulesDirty = false;
  let rulesLoading = false;
  let rulesAbort = null;
  let rulesSequence = 0;

  function vpnControls() {
    const blocked = !vpnAvailable || vpnBusy || vpnUpdating || rulesLoading || !authenticated || rebootPending;
    byId("vpn-settings").disabled = blocked;
    byId("routing-settings").disabled = blocked || !rulesLoaded;
    byId("routing-apply").disabled = !rulesDirty;
    byId("routing-load").disabled = blocked;
    byId("routing-load").textContent = rulesLoading ? "Загружаем…" : rulesDirty ? "Загрузить списки заново (заменит черновик)" : "Загрузить мои исключения";
    byId("routing-refresh").disabled = blocked;
    byId("vpn-form").setAttribute("aria-busy", String(vpnBusy));
    byId("routing-form").setAttribute("aria-busy", String(vpnBusy || rulesLoading));
    byId("vpn-controls").querySelectorAll("[data-toggle-secret]").forEach(button => { button.disabled = vpnBusy; });
  }

  function localDate(value) {
    if (!value || !Number.isFinite(Date.parse(value))) return "—";
    return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
  }

  function renderVpn(data) {
    vpnAvailable = Boolean(data.available);
    vpnUpdating = Boolean(data.updating);
    document.body.dataset.state = vpnAvailable ? "online" : "offline";
    text("connection", vpnAvailable ? "В сети" : "Нет данных");
    text("vpn-state", !vpnAvailable ? "Нет данных" : vpnUpdating ? "Применяем настройки" : data.online === true ? "Подключён" : data.online === false ? "VPN недоступен" : "Проверяем…");
    text("vpn-check-time", data.checked_at ? `HTTPS через VPN · ${localDate(data.checked_at)}` : "Проверка интернета выполняется каждые 30 сек");
    text("vpn-clients", Number.isInteger(data.clients) ? data.clients : "—");
    text("vpn-down", Number.isFinite(data.download_bps) ? rate(data.download_bps) : "—");
    text("vpn-up", Number.isFinite(data.upload_bps) ? rate(data.upload_bps) : "—");
    text("vpn-latency", data.online && Number.isFinite(data.latency_ms) ? `${data.latency_ms} мс` : "—");
    text("vpn-wifi-state", data.wifi_active ? "Точка доступа работает" : "Точка доступа недоступна");
    text("vpn-ssid", data.ssid || "—");
    text("vpn-band", data.band ? `${data.band} · канал ${data.channel}` : "—");
    text("vpn-security", data.security || "—");
    text("vpn-service", data.service_running ? "Работает" : "Не работает");
    text("vpn-protection", data.protection_active ? "Включена" : "Требуется проверка");
    text("vpn-key-time", localDate(data.key_updated_at));
    text("vpn-status-note", !vpnAvailable ? "Контроллер временно недоступен. Управление заблокировано до восстановления связи." : vpnUpdating ? "Проверяются или применяются настройки VPN. Wi-Fi-настройки сохраняются." : "Пароли, ключи, адреса сервера и данные отдельных устройств здесь не публикуются.");
    const routes = data.routing;
    text("routing-mode", routes?.enabled ? "По умолчанию — VPN" : "Исключения не установлены");
    text("routing-groups", routes?.enabled ? routes.groups.join(" · ") : "Ожидаем данные о базовых исключениях.");
    text("routing-base", routes?.enabled ? `${routes.base_domains} доменных правил` : "—");
    text("routing-custom", routes ? `${routes.direct_count} / ${routes.vpn_count}` : "—");
    text("routing-updated", localDate(routes?.updated_at));
    text("routing-checked", routes?.last_check ? `${localDate(routes.last_check.checked_at)}${routes.last_check.ok ? " · актуально" : " · не удалось, прежние правила сохранены"}` : "Ежедневная проверка ещё не выполнялась");
    for (const kind of ["direct", "vpn"]) {
      const traffic = data.route_traffic?.[kind];
      text(`routing-${kind}-traffic`, traffic ? `${bytes(traffic.down)} / ${bytes(traffic.up)}` : "—");
    }
    text("updated", data.updated_at ? `Обновлено ${localDate(data.updated_at)}` : "Ожидание данных VPN");
    vpnControls();
  }

  async function refreshVpn() {
    vpnAbort?.abort();
    const request = new AbortController();
    vpnAbort = request;
    const sequence = ++vpnSequence;
    const timeout = window.setTimeout(() => request.abort(), 4500);
    try {
      const response = await fetch("/api/vpn", { cache: "no-store", signal: request.signal });
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (sequence === vpnSequence) renderVpn(data);
    } catch (_) {
      if (sequence === vpnSequence) {
        vpnAvailable = false;
        vpnControls();
        text("connection", "Нет связи");
        text("vpn-status-note", "Нет связи с сайтом · показаны последние измерения. Повторяем подключение.");
        document.body.dataset.state = "offline";
      }
    } finally {
      window.clearTimeout(timeout);
      if (sequence === vpnSequence) vpnTimer = window.setTimeout(refreshVpn, 5000);
    }
  }

  function vpnFeedback(message, error = false) {
    text("vpn-feedback", message);
    byId("vpn-feedback").dataset.error = String(error);
  }

  async function pollVpnJob(id) {
    const request = new AbortController();
    const timeout = window.setTimeout(() => request.abort(), 6000);
    try {
      const response = await fetch(`/api/vpn/operation?request_id=${encodeURIComponent(id)}`, {
        cache: "no-store", signal: request.signal,
        headers: {},
      });
      if (response.status === 401) {
        vpnFeedback("Сессия истекла. Войдите снова и проверьте текущий статус VPN.", true);
        vpnBusy = false; requireLogin(); vpnControls(); return;
      }
      if (!response.ok) throw new Error();
      const result = await response.json();
      if (result.job) {
        vpnFeedback(result.job.message, result.job.state === "error");
        if (["success", "error"].includes(result.job.state)) {
          vpnBusy = false;
          vpnUpdating = false;
          vpnControls();
          window.clearTimeout(vpnTimer);
          refreshVpn();
          if (vpnAction === "rules" && result.job.state === "success") loadRules(false);
          return;
        }
      }
    } catch (_) {
      vpnFeedback("Ожидаем подтверждение от сервера. После переключения соединение восстановится; повторно отправлять настройки не нужно.");
    } finally {
      window.clearTimeout(timeout);
    }
    if (Date.now() < vpnJobDeadline) vpnJobTimer = window.setTimeout(() => pollVpnJob(id), 1200);
    else {
      vpnBusy = false; vpnControls();
      vpnFeedback("Подтверждение пока недоступно. Проверьте текущий статус VPN перед новой попыткой.", true);
    }
  }

  byId("vpn-uri").addEventListener("input", () => { byId("vpn-uri").removeAttribute("aria-invalid"); });
  byId("vpn-form").addEventListener("submit", async event => {
    event.preventDefault();
    if (vpnBusy || vpnUpdating || !vpnAvailable) return;
    const uri = byId("vpn-uri").value.trim();
    if (!authenticated) { requireLogin(); return; }
    if (!/^vless:\/\//i.test(uri)) {
      byId("vpn-uri").setAttribute("aria-invalid", "true");
      vpnFeedback("Вставьте полную ссылку, начинающуюся с vless://", true);
      byId("vpn-uri").focus(); return;
    }
    const id = crypto.randomUUID();
    vpnAction = "key";
    vpnBusy = true; vpnControls();
    vpnFeedback("Отправляем ключ на проверку…");
    const request = new AbortController();
    const timeout = window.setTimeout(() => request.abort(), 7000);
    try {
      const response = await fetch("/api/vpn/config", {
        method: "POST", cache: "no-store", signal: request.signal,
        headers: { "Content-Type": "application/json",  },
        body: JSON.stringify({ uri, request_id: id }),
      });
      const result = await response.json();
      if (!response.ok) {
        vpnBusy = false;
        if (response.status === 401) { requireLogin(); }
        else if (response.status === 400) { byId("vpn-uri").setAttribute("aria-invalid", "true"); }
        vpnFeedback(result.error || "Ключ не принят. Проверьте текущий статус VPN.", true);
        vpnControls();
        (response.status === 401 ? byId("auth-password") : byId("vpn-uri")).focus();
        return;
      }
      byId("vpn-uri").value = "";
      byId("vpn-uri").type = "password";
      const toggle = document.querySelector('[data-toggle-secret="vpn-uri"]');
      toggle.textContent = "Показать"; toggle.setAttribute("aria-pressed", "false");
      vpnFeedback(result.job.message);
    } catch (_) {
      vpnFeedback("Нет подтверждения приёма ключа. Проверяем результат операции; повторно отправлять ключ не нужно.");
    } finally {
      window.clearTimeout(timeout);
    }
    vpnJobDeadline = Date.now() + 120000;
    window.clearTimeout(vpnJobTimer);
    pollVpnJob(id);
  });

  async function loadRules(announce = true) {
    if (vpnBusy || rulesLoading || !authenticated) return;
    rulesAbort?.abort();
    const request = new AbortController(); rulesAbort = request;
    const sequence = ++rulesSequence;
    const timeout = window.setTimeout(() => request.abort(), 7000);
    rulesLoading = true; vpnControls();
    try {
      const response = await fetch("/api/vpn/rules", {cache: "no-store", signal: request.signal, headers: {}});
      const result = await response.json();
      if (sequence !== rulesSequence) return;
      if (!response.ok) {
        if (response.status === 401) { requireLogin(); }
        vpnFeedback(result.error || "Не удалось загрузить исключения", true); return;
      }
      for (const kind of ["direct", "vpn"]) { byId(`routing-${kind}`).value = result.rules[kind].join("\n"); byId(`routing-${kind}`).removeAttribute("aria-invalid"); }
      rulesRevision = result.rules.revision; rulesLoaded = true; rulesDirty = false;
      text("routing-draft", "Загружены действующие списки. Изменения применяются кнопкой сохранения.");
      if (announce) vpnFeedback("Ваши исключения загружены. Пустой список означает отсутствие ручных дополнений.");
    } catch (_) {
      if (sequence === rulesSequence) vpnFeedback("Не удалось загрузить исключения. Черновик сохранён; проверьте связь и повторите загрузку.", true);
    } finally {
      window.clearTimeout(timeout);
      if (sequence === rulesSequence) { rulesLoading = false; vpnControls(); }
    }
  }
  byId("routing-load").addEventListener("click", () => loadRules());
  for (const id of ["routing-direct", "routing-vpn"]) byId(id).addEventListener("input", () => {
    rulesDirty = true; byId(id).removeAttribute("aria-invalid");
    text("routing-draft", "Есть несохранённые изменения. Они останутся на странице до сохранения или перезагрузки.");
    vpnControls();
  });

  async function submitRules(refresh = false) {
    if (vpnBusy || vpnUpdating || rulesLoading || !vpnAvailable || !authenticated || (!refresh && !rulesLoaded)) return;
    const id = crypto.randomUUID();
    const payload = {request_id: id};
    if (!refresh) {
      for (const kind of ["direct", "vpn"]) {
        const input = byId(`routing-${kind}`);
        payload[kind] = input.value.split(/\r?\n/).map(value => value.trim()).filter(Boolean);
        if (payload[kind].length > 50 || payload[kind].some(value => /[\s/:*?#@]/.test(value) || !value.includes("."))) {
          input.setAttribute("aria-invalid", "true"); input.focus();
          vpnFeedback("Укажите до 50 доменов в каждом списке, по одному на строку, без ссылки и пути.", true); return;
        }
      }
      payload.revision = rulesRevision;
    }
    vpnAction = refresh ? "refresh" : "rules";
    vpnBusy = true; vpnControls();
    vpnFeedback(refresh ? "Проверяем обновления базового списка…" : "Отправляем исключения на проверку…");
    const request = new AbortController();
    const timeout = window.setTimeout(() => request.abort(), 7000);
    try {
      const response = await fetch(refresh ? "/api/vpn/rules/refresh" : "/api/vpn/rules", {method: "POST", cache: "no-store", signal: request.signal,
        headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
      const result = await response.json();
      if (!response.ok) {
        vpnBusy = false;
        if (response.status === 401) { requireLogin(); }
        if (response.status === 400 && !refresh) { byId("routing-direct").setAttribute("aria-invalid", "true"); byId("routing-direct").focus(); }
        vpnFeedback(result.error || "Настройки не приняты. Черновик сохранён.", true); vpnControls(); return;
      }
      vpnFeedback(result.job.message);
    } catch (_) {
      vpnFeedback("Нет подтверждения приёма настроек. Проверяем результат; повторная отправка не требуется.");
    } finally { window.clearTimeout(timeout); }
    vpnJobDeadline = Date.now() + 180000;
    window.clearTimeout(vpnJobTimer); pollVpnJob(id);
  }
  byId("routing-form").addEventListener("submit", event => { event.preventDefault(); submitRules(); });
  byId("routing-refresh").addEventListener("click", () => submitRules(true));

  function uptime(seconds) {
    let left = Math.max(0, Math.floor(Number(seconds) || 0));
    const days = Math.floor(left / 86400); left %= 86400;
    const hours = Math.floor(left / 3600); left %= 3600;
    const minutes = Math.floor(left / 60);
    if (days > 0) return `${days} д ${hours} ч ${minutes} мин`;
    if (hours > 0) return `${hours} ч ${minutes} мин`;
    return `${minutes} мин`;
  }

  function drawTrace(value) {
    history.push(clamp(value, 0, 100));
    if (history.length > 40) history.shift();
    const step = history.length > 1 ? 640 / (history.length - 1) : 640;
    const points = history.map((item, index) => `${(index * step).toFixed(1)},${(112 - item * 1.02).toFixed(1)}`).join(" ");
    byId("trace-line").setAttribute("points", points);
    byId("trace-fill").setAttribute("points", points);
    byId("trace").setAttribute("aria-label", `История загрузки процессора, сейчас ${percent(value)}`);
  }

  function renderCores(cpu, updatedAt) {
    const cores = Array.isArray(cpu.per_core) ? cpu.per_core.filter(core => Number.isInteger(core.id) && core.id >= 0) : [];
    if (!cores.length) {
      text("core-status", "Измерения по ядрам временно недоступны.");
      return;
    }
    const stamp = Date.parse(updatedAt);
    if (!Number.isFinite(stamp)) return;
    const fresh = stamp > lastCoreStamp;
    const active = new Set(cores.map(core => core.id));
    for (const [id, view] of coreViews) {
      if (!active.has(id)) { view.element.remove(); coreViews.delete(id); }
    }
    for (const core of cores) {
      let view = coreViews.get(core.id);
      if (!view) {
        const element = byId("core-template").content.firstElementChild.cloneNode(true);
        const label = `Ядро ${core.id + 1}`;
        element.dataset.coreId = String(core.id);
        element.querySelector("h3").textContent = label;
        element.querySelector(".core-id").textContent = `CPU ${core.id}`;
        element.querySelector("progress").setAttribute("aria-label", `Загрузка: ${label.toLowerCase()}`);
        view = { element, label, samples: [] };
        coreViews.set(core.id, view);
        byId("core-grid").append(element);
      }
      const value = Number.isFinite(core.percent) ? clamp(core.percent, 0, 100) : null;
      if (fresh) view.samples.push({ stamp, value });
      view.samples = view.samples.filter(sample => sample.stamp >= stamp - 120000).slice(-41);
      view.element.querySelector("strong").textContent = value === null ? "—" : `${coreNumber.format(value)}%`;
      view.element.querySelector("progress").value = value ?? 0;
      view.element.dataset.busy = String(value !== null && value >= 85);
      view.element.querySelector(".core-condition").textContent = value === null ? "Сбор данных" : value >= 85 ? "Высокая нагрузка" : value >= 40 ? "В работе" : "Низкая нагрузка";
      const points = [];
      let connected = false;
      let previousStamp = 0;
      for (const sample of view.samples) {
        if (sample.value === null) { connected = false; continue; }
        const x = 240 - (stamp - sample.stamp) / 500;
        const y = 66 - sample.value * .6;
        points.push(`${connected && sample.stamp - previousStamp <= 9000 ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`);
        connected = true;
        previousStamp = sample.stamp;
      }
      view.element.querySelector("path").setAttribute("d", points.join(" "));
      const dot = view.element.querySelector("circle");
      dot.setAttribute("cx", "240");
      dot.setAttribute("cy", String(66 - (value ?? 0) * .6));
      dot.setAttribute("visibility", value === null ? "hidden" : "visible");
      view.element.querySelector("svg").setAttribute("aria-label", `${view.label}: ${value === null ? "ожидание данных" : coreNumber.format(value) + "%"}. История загрузки за последние две минуты, шкала от 0 до 100%.`);
    }
    if (fresh) lastCoreStamp = stamp;
    const measured = cores.filter(core => Number.isFinite(core.percent));
    const busiest = measured.reduce((best, core) => !best || core.percent > best.percent ? core : best, null);
    text("core-summary", busiest ? `Максимум · ядро ${busiest.id + 1} · ${coreNumber.format(busiest.percent)}%` : "Начинаем измерение");
    text("core-status", measured.length ? "Шкала каждого графика: 0–100%. История накапливается, пока открыта страница." : "Ожидаем следующий замер для расчёта загрузки.");
  }

  function updateFanControls() {
    byId("fan-settings").disabled = fanBusy || !fanAvailable || !authenticated || !serverAvailable || rebootPending;
    byId("fan-speed-control").hidden = byId("fan-form").elements["fan-mode"].value !== "manual";
  }

  function renderFan(fan) {
    fanAvailable = Boolean(fan?.available);
    text("fan-rpm", Number.isFinite(fan?.rpm) ? new Intl.NumberFormat("ru-RU").format(fan.rpm) : "—");
    text("fan-pwm", Number.isFinite(fan?.pwm_percent) ? `${fan.pwm_percent}% мощности` : "—");
    text("fan-mode", !fanAvailable ? "Контроллер временно недоступен" : fan.mode === "manual" ? `Вручную · ещё ${Math.ceil(fan.manual_remaining_seconds / 60)} мин` : "Автоматически · по температуре");
    text("fan-safety", fan?.safety === "overheat" ? "Термозащита: 100% до охлаждения процессора до 70 °C." : fan?.safety === "sensor" ? "Нет данных датчика: защитный режим 100%." : fan?.error || "");
    updateFanControls();
  }

  byId("fan-form").addEventListener("change", updateFanControls);
  byId("fan-speed").addEventListener("input", () => { byId("fan-speed-value").value = `${byId("fan-speed").value}%`; });
  byId("fan-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (fanBusy || !fanAvailable) return;
    if (!authenticated) { requireLogin(); return; }
    const mode = byId("fan-form").elements["fan-mode"].value;
    const payload = { mode };
    if (mode === "manual") payload.percent = Number(byId("fan-speed").value);
    fanBusy = true;
    updateFanControls();
    byId("fan-form").setAttribute("aria-busy", "true");
    byId("fan-feedback").dataset.error = "false";
    text("fan-feedback", "Применяем режим…");
    const timeout = new AbortController();
    const timeoutId = window.setTimeout(() => timeout.abort(), 7000);
    try {
      const response = await fetch("/api/fan", {
        method: "POST", cache: "no-store", signal: timeout.signal,
        headers: { "Content-Type": "application/json",  },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok) {
        if (response.status === 401) { requireLogin(); }
        throw new Error(result.error || "Не удалось применить режим");
      }
      renderFan(result.fan);
      text("fan-feedback", result.fan.safety ? "Режим принят. Сейчас действует термозащита, скорость 100%." : mode === "manual" ? `Установлено ${payload.percent}% на 30 минут.` : "Включён автоматический режим.");
    } catch (error) {
      byId("fan-feedback").dataset.error = "true";
      text("fan-feedback", error.name === "AbortError" || error instanceof TypeError ? "Нет подтверждения от сервера. Проверьте текущий режим выше перед повтором." : error.message);
    } finally {
      window.clearTimeout(timeoutId);
      fanBusy = false;
      byId("fan-form").removeAttribute("aria-busy");
      updateFanControls();

    }
  });

  function render(data) {
    serverAvailable = true; updateRebootControls();
    renderCores(data.cpu, data.updated_at);
    renderFan(data.fan);
    document.body.dataset.state = data.status === "healthy" ? "online" : "attention";
    text("connection", data.status === "healthy" ? "В сети" : "Нужна проверка");
    text("state-word", data.status === "healthy" ? "на связи." : "под нагрузкой.");
    text("uptime", uptime(data.uptime_seconds));

    const temperature = data.temperature_c === null ? Number.NaN : Number(data.temperature_c);
    text("temperature", Number.isFinite(temperature) ? temperature.toFixed(1) : "—");
    byId("temperature-bar").value = Number.isFinite(temperature) ? clamp(temperature, 0, 100) : 0;
    text("temp-state", !Number.isFinite(temperature) ? "нет датчика" : temperature >= 80 ? "горячо" : temperature >= 65 ? "тепло" : "норма");
    text("throttle", data.throttle.ok ? "без ограничений" : `флаги ${data.throttle.code}`);

    text("cpu", percent(data.cpu.percent));
    byId("cpu-bar").value = clamp(data.cpu.percent, 0, 100);
    text("cores", data.cpu.cores);
    text("frequency", data.cpu.frequency_mhz ? (data.cpu.frequency_mhz / 1000).toFixed(2) : "—");
    drawTrace(data.cpu.percent);
    text("load", `${data.cpu.load1.toFixed(2)} / ${data.cpu.load5.toFixed(2)} / ${data.cpu.load15.toFixed(2)}`);

    text("memory", percent(data.memory.percent));
    byId("memory-bar").value = clamp(data.memory.percent, 0, 100);
    text("memory-detail", `${bytes(data.memory.used)} из ${bytes(data.memory.total)}`);
    text("swap", `${bytes(data.memory.swap_used)} из ${bytes(data.memory.swap_total)}`);

    text("disk", percent(data.disk.percent));
    byId("disk-bar").value = clamp(data.disk.percent, 0, 100);
    text("disk-detail", bytes(data.disk.free));

    text("containers", `${data.docker.running} / ${data.docker.total}`);
    text("container-health", `${data.docker.healthy} health-check OK`);
    text("network-down", rate(data.network.rx_bps));
    text("network-up", rate(data.network.tx_bps));
    text("host", data.host);
    text("platform", `${data.platform} · ${data.arch.toUpperCase()}`);
    text("machine-model", data.machine.model);
    text("machine-arch", data.arch.toUpperCase());
    text("cpu-model", data.machine.cpu_model);
    text("cpu-spec", data.cpu.frequency_mhz ? `${data.cpu.cores} ядра · ${(data.cpu.frequency_mhz / 1000).toFixed(2)} ГГц сейчас` : `${data.cpu.cores} ядра · частота недоступна`);
    text("os-detail", `${data.platform} · ${data.arch.toUpperCase()}`);
    text("kernel", data.machine.kernel);
    const booted = new Date(data.machine.booted_at);
    const bootFormat = new Intl.DateTimeFormat("ru-RU", {
      day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
      timeZone: data.machine.timezone,
    });
    text("boot-time", bootFormat.format(booted));
    text("server-timezone", data.machine.timezone);
    text("ram-total", bytes(data.memory.total));
    text("swap-total", `swap ${bytes(data.memory.swap_total)}`);
    text("disk-total", bytes(data.disk.total));
    text("disk-free", `${bytes(data.disk.free)} свободно`);
    text("docker-engine", data.machine.docker_version ? `Docker ${data.machine.docker_version}` : "Docker Engine");
    text("docker-state", `${data.docker.running} контейнеров запущено`);
    text("systemd", data.machine.failed_units === 0 ? "0 сбоев" : `${data.machine.failed_units} сбоев`);
    text("systemd-detail", data.machine.failed_units === 0 ? "службы без ошибок" : "требуется проверка");
    const stamp = new Date(data.updated_at);
    text("updated", `Обновлено ${new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(stamp)}`);
  }

  function setOffline() {
    serverAvailable = false; updateRebootControls();
    text("core-status", "Нет связи · показаны последние измерения ядер.");
    fanAvailable = false;
    updateFanControls();
    text("fan-mode", "Нет связи · показаны последние данные");
    document.body.dataset.state = "offline";
    text("connection", "Нет связи");
    text("updated", "Телеметрия временно недоступна · повторяем подключение");
  }

  async function refresh() {
    controller?.abort();
    controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 4500);
    try {
      const response = await fetch("/api/status", { cache: "no-store", signal: controller.signal });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (error) {
      if (error.name !== "AbortError" || !document.hidden) setOffline();
    } finally {
      window.clearTimeout(timeout);
      timer = window.setTimeout(refresh, 3000);
    }
  }

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      if (vpnPage) { window.clearTimeout(vpnTimer); refreshVpn(); return; }
      window.clearTimeout(timer);
      refresh();
    }
  });

  function renderAuth() {
    byId("auth-form").hidden = authenticated;
    byId("auth-logout").hidden = !authenticated;
    byId("auth-password").disabled = authBusy;
    byId("auth-login").disabled = authBusy;
    byId("auth-logout").disabled = authBusy || vpnBusy || fanBusy || rebootPending;
    byId("auth-form").setAttribute("aria-busy", String(authBusy));
    byId("auth-disclosure").querySelector("summary").textContent = authenticated ? "Управление доступно · admin" : "Вход для управления";
    vpnControls(); updateFanControls(); updateRebootControls();
  }

  function requireLogin() {
    authenticated = false; renderAuth();
    byId("auth-disclosure").open = true;
    text("auth-feedback", "Войдите с паролем sudo пользователя admin. Черновики сохранены на странице.");
    byId("auth-password").focus();
  }

  async function refreshAuth() {
    const version = authVersion;
    const abort = new AbortController();
    const timeout = window.setTimeout(() => abort.abort(), 6000);
    try {
      const response = await fetch("/api/auth", {cache: "no-store", signal: abort.signal});
      if (!response.ok) throw new Error();
      const data = await response.json();
      if (version !== authVersion) return;
      const expired = authenticated && !data.authenticated;
      authenticated = Boolean(data.authenticated);
      if (data.reboot) showRebootPending();
      if (!authBusy && !authMessageLocked) text("auth-feedback", authenticated ? "Вход выполнен. Сессия действует до 10 минут; пароль на сайте не сохраняется." : expired ? "Сессия истекла. Войдите снова; черновики сохранены." : "Просмотр открыт всем. Для изменений войдите с паролем sudo.");
      renderAuth();
    } catch (_) {
      if (version !== authVersion) return;
      authenticated = false; renderAuth();
      if (!authBusy) text("auth-feedback", "Нет связи со службой входа. Изменения недоступны; повторяем проверку.");
    } finally {
      window.clearTimeout(timeout);
      window.clearTimeout(authTimer); authTimer = window.setTimeout(refreshAuth, 15000);
    }
  }

  byId("auth-password").addEventListener("input", () => { byId("auth-password").removeAttribute("aria-invalid"); authMessageLocked = false; });
  byId("auth-form").addEventListener("submit", async event => {
    event.preventDefault(); if (authBusy) return;
    const password = byId("auth-password").value;
    if (!password) { byId("auth-password").setAttribute("aria-invalid", "true"); byId("auth-password").focus(); text("auth-feedback", "Введите пароль sudo пользователя admin."); return; }
    authVersion++; authBusy = true; renderAuth(); text("auth-feedback", "Проверяем системный пароль…");
    const abort = new AbortController(); const timeout = window.setTimeout(() => abort.abort(), 12000);
    try {
      const response = await fetch("/api/auth/login", {method: "POST", cache: "no-store", signal: abort.signal, headers: {"Content-Type": "application/json"}, body: JSON.stringify({password})});
      const result = await response.json();
      if (!response.ok) { byId("auth-password").setAttribute("aria-invalid", "true"); throw new Error(result.error || "Не удалось войти"); }
      authenticated = true;
      byId("auth-password").value = ""; byId("auth-password").type = "password";
      const toggle = document.querySelector('[data-toggle-secret="auth-password"]'); toggle.textContent = "Показать"; toggle.setAttribute("aria-pressed", "false");
      text("auth-feedback", "Вход выполнен. Управление доступно на всех трёх вкладках в течение 10 минут.");
      byId("auth-disclosure").open = false;
    } catch (error) {
      authenticated = false;
      authMessageLocked = true;
      text("auth-feedback", error.name === "AbortError" || error instanceof TypeError ? "Нет подтверждения входа. Проверьте связь и повторите попытку." : error.message);
    } finally {
      window.clearTimeout(timeout); authBusy = false; renderAuth();
      if (!authenticated) byId("auth-password").focus();
    }
  });
  byId("auth-logout").addEventListener("click", async () => {
    if (authBusy) return; authVersion++; authBusy = true; renderAuth();
    const abort = new AbortController(); const timeout = window.setTimeout(() => abort.abort(), 6000);
    try {
      const response = await fetch("/api/auth/logout", {method: "POST", signal: abort.signal, headers: {"Content-Type": "application/json"}, body: "{}"});
      if (!response.ok) throw new Error();
      authenticated = false;
      rulesAbort?.abort(); rulesSequence++; rulesLoading = false; rulesLoaded = false; rulesDirty = false; rulesRevision = null;
      ["routing-direct", "routing-vpn", "vpn-uri", "auth-password"].forEach(id => { byId(id).value = ""; });
      text("routing-draft", "Выполнен выход. Черновики удалены из памяти страницы.");
      text("auth-feedback", "Вы вышли из управления. Сайт, VPN и вентилятор продолжают работать.");
    } catch (_) { text("auth-feedback", "Выход не подтверждён. Проверьте связь и повторите попытку."); }
    finally { window.clearTimeout(timeout); authBusy = false; renderAuth(); }
  });

  function updateRebootControls() {
    const disabled = !authenticated || !serverAvailable || rebootBusy || rebootPending || fanBusy || vpnBusy;
    byId("reboot-open").disabled = disabled;
    byId("reboot-confirm").disabled = disabled;
    text("manage-state", rebootPending ? "Перезагрузка запланирована" : serverAvailable ? "Сервер на связи" : "Нет связи");
  }
  function showRebootPending() {
    rebootPending = true;
    text("reboot-feedback", "Перезагрузка запланирована. Сайт и Wi-Fi временно отключатся. После восстановления обновите страницу и войдите снова.");
    updateRebootControls(); vpnControls(); updateFanControls();
  }
  byId("reboot-open").addEventListener("click", () => {
    if (byId("reboot-open").disabled) return;
    byId("reboot-dialog").showModal(); byId("reboot-cancel").focus();
  });
  byId("reboot-cancel").addEventListener("click", () => byId("reboot-dialog").close());
  byId("reboot-dialog").addEventListener("close", () => { if (!rebootPending) byId("reboot-open").focus(); });
  byId("reboot-confirm").addEventListener("click", async () => {
    if (byId("reboot-confirm").disabled) return;
    rebootBusy = true; updateRebootControls(); byId("reboot-dialog").close();
    text("reboot-feedback", "Отправляем команду перезагрузки…");
    const abort = new AbortController(); const timeout = window.setTimeout(() => abort.abort(), 7000);
    try {
      const response = await fetch("/api/admin/reboot", {method: "POST", cache: "no-store", signal: abort.signal, headers: {"Content-Type": "application/json"}, body: JSON.stringify({confirm: true, request_id: crypto.randomUUID()})});
      const result = await response.json();
      if (!response.ok) { if (response.status === 401) requireLogin(); if (result.uncertain) rebootPending = true; throw new Error(result.error || "Перезагрузка не подтверждена"); }
      showRebootPending();
    } catch (error) {
      text("reboot-feedback", error.name === "AbortError" || error instanceof TypeError ? "Результат команды неизвестен. Проверяем состояние сервера; повторно не нажимайте перезагрузку." : error.message);
      // Do not automatically repeat this mutation, including after a timeout.
      if (error.name === "AbortError" || error instanceof TypeError) rebootPending = true;
      refreshAuth();
    } finally { window.clearTimeout(timeout); rebootBusy = false; updateRebootControls(); }
  });

  async function refreshStartup() {
    const abort = new AbortController(); const timeout = window.setTimeout(() => abort.abort(), 6000);
    try {
      const response = await fetch("/api/manage", {cache: "no-store", signal: abort.signal});
      if (!response.ok) throw new Error();
      const data = await response.json();
      const fragment = document.createDocumentFragment();
      for (const service of data.services) {
        const row = document.createElement("div"); const title = document.createElement("dt"); const value = document.createElement("dd");
        title.textContent = service.label;
        value.className = "service-statuses";
        value.append(stateReading("Работа", service.running === true ? "Работает" : service.running === false ? "Остановлена" : "Нет данных", signalState(service.running)),
          stateReading("Автозапуск", service.enabled === true ? "Включён" : service.enabled === false ? "Выключен" : "Нет данных", signalState(service.enabled, true)));
        row.append(title, value); fragment.append(row);
      }
      byId("startup-services").replaceChildren(fragment);
      text("startup-state", data.services.length && data.services.every(item => item.enabled) ? "Все службы включены" : "Требуется проверка");
    } catch (_) {
      text("startup-state", "Нет связи · статус не подтверждён");
      byId("startup-services").querySelectorAll(".status-light").forEach(lamp => { lamp.dataset.state = "unknown"; lamp.textContent = "Нет данных"; });
    }
    finally { window.clearTimeout(timeout); window.setTimeout(refreshStartup, 15000); }
  }
  function signalState(value, optional = false) {
    return value === true ? "on" : value === false ? optional ? "warn" : "off" : "unknown";
  }
  function statusLight(label, state) {
    const element = document.createElement("span");
    element.className = "status-light"; element.dataset.state = state; element.textContent = label;
    return element;
  }
  function stateReading(label, value, state) {
    const reading = document.createElement("span"); reading.className = "state-reading";
    const caption = document.createElement("small"); caption.textContent = label;
    reading.append(caption, statusLight(value, state)); return reading;
  }
  function renderProjects(data) {
    const fresh = Boolean(data.available && Math.abs(Date.now() - Date.parse(data.updated_at)) < 45000);
    document.querySelectorAll("[data-project-list]").forEach(list => {
      const fragment = document.createDocumentFragment();
      for (const project of data.projects || []) {
        const item = document.createElement("article"); item.className = "project-item";
        const heading = document.createElement("div"); heading.className = "project-heading";
        const title = document.createElement("h3"); title.textContent = project.name;
        const state = fresh ? project.state : "unknown";
        heading.append(title, statusLight(({up:"Работает", attention:"Требует внимания", down:"Остановлен", unknown:"Нет данных"})[state] || "Нет данных", ({up:"on",attention:"warn",down:"off"})[state] || "unknown"));
        const readings = document.createElement("dl"); readings.className = "project-readings";
        const values = [
          ["Контейнеры", fresh && Number.isInteger(project.total) ? `${project.running} из ${project.total}` : "Нет данных", fresh && project.total > 0 ? project.running === project.total ? "on" : "off" : "unknown"],
          ["HTTP", fresh && project.http_code ? `Ответ ${project.http_code}` : fresh && project.http_online === false ? "Нет ответа" : "Нет данных", signalState(fresh ? project.http_online : null)],
          ["Автозапуск", fresh && project.autostart === true ? "Включён" : fresh && project.autostart === false ? "Выключен" : "Нет данных", signalState(fresh ? project.autostart : null, true)],
        ];
        if (project.id === "img2txt") {
          values.push(["VPN", fresh && project.vpn_online === true ? "Доступен" : fresh && project.vpn_online === false ? "Нет соединения" : "Нет данных", signalState(fresh ? project.vpn_online : null)]);
          values.push(["Ключ VPN", fresh && project.vpn_synced === true ? "Основной · синхронизирован" : fresh && project.vpn_synced === false ? "Ошибка синхронизации" : "Нет данных", signalState(fresh ? project.vpn_synced : null)]);
          const llm = fresh ? project.llm_state : "unknown";
          values.push(["LLM API", ({ready:"Готов",checking:"Проверяем…",invalid_key:"Неверный ключ API",missing_key:"Нужен ключ API",denied:"Доступ запрещён",rate_limited:"Лимит запросов",model_unavailable:"Модель недоступна",unavailable:"Нет соединения"})[llm] || "Нет данных", llm === "ready" ? "on" : ["checking","rate_limited"].includes(llm) ? "warn" : llm === "unknown" ? "unknown" : "off"]);
        }
        for (const [label, value, lamp] of values) {
          const row = document.createElement("div"), term = document.createElement("dt"), description = document.createElement("dd");
          term.textContent = label; description.append(statusLight(value, lamp)); row.append(term, description); readings.append(row);
        }
        const detail = document.createElement("p"); detail.className = "project-detail";
        detail.textContent = fresh && Number.isInteger(project.health_checks) ? project.health_checks ? `Проверки контейнеров: ${project.healthy} из ${project.health_checks} в норме. HTTP проверяется отдельно.` : "Контейнеры без встроенных проверок здоровья. Доступность проверяется по HTTP." : "Состояние не подтверждено. Повторяем проверку.";
        item.append(heading, readings, detail); fragment.append(item);
      }
      if (!fragment.childNodes.length) { const empty = document.createElement("p"); empty.className = "fan-help"; empty.textContent = "Данные проектов пока недоступны. Повторяем проверку."; fragment.append(empty); }
      list.replaceChildren(fragment);
    });
    document.querySelectorAll("[data-project-summary]").forEach(label => { label.textContent = fresh ? `${data.projects.length} проект${data.projects.length === 1 ? "" : "а"}` : "Нет свежих данных"; });
    document.querySelectorAll("[data-project-updated]").forEach(label => { label.textContent = fresh ? `Проверено ${localDate(data.updated_at)} · каждые 15 секунд` : "Нет связи · статусы не подтверждены"; });
  }
  async function refreshProjects() {
    const abort = new AbortController(); const timeout = window.setTimeout(() => abort.abort(), 6000);
    try {
      const response = await fetch("/api/projects", {cache:"no-store", signal:abort.signal});
      if (!response.ok) throw new Error();
      renderProjects(await response.json());
    } catch (_) {
      document.querySelectorAll("[data-project-list] .status-light").forEach(lamp => { lamp.dataset.state = "unknown"; lamp.textContent = "Нет данных"; });
      document.querySelectorAll("[data-project-summary]").forEach(label => { label.textContent = "Нет связи"; });
      document.querySelectorAll("[data-project-updated]").forEach(label => { label.textContent = "Нет связи · статусы не подтверждены"; });
    } finally { window.clearTimeout(timeout); window.setTimeout(refreshProjects, 15000); }
  }
  refreshAuth();
  if (managePage) refreshStartup();
  if (!vpnPage) refreshProjects();
  if (vpnPage) refreshVpn(); else refresh();
})();
'''


def _read_key_values(path: str) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                if not value:
                    continue
                token = value.strip().split()[0]
                if token.isdigit():
                    result[key] = int(token) * 1024
    except OSError:
        pass
    return result


def _cpu_times() -> dict[str, tuple[int, int]]:
    result = {}
    with open("/proc/stat", "r", encoding="ascii") as handle:
        for line in handle:
            fields = line.split()
            if not fields or not fields[0].startswith("cpu"):
                break
            name = fields[0]
            if name != "cpu" and not name[3:].isdigit():
                continue
            try:
                # guest/guest_nice are already included in user/nice.
                parts = [int(item) for item in fields[1:9]]
                if len(parts) < 4 or any(part < 0 for part in parts):
                    continue
                idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
                result[name] = (sum(parts), idle)
            except ValueError:
                continue
    return result


def _cpu_percent(previous: tuple[int, int] | None, current: tuple[int, int]) -> float | None:
    if previous is None:
        return None
    total_delta = current[0] - previous[0]
    idle_delta = current[1] - previous[1]
    if total_delta <= 0 or idle_delta < 0 or idle_delta > total_delta:
        return None
    return round(100 * (1 - idle_delta / total_delta), 1)


def _network_bytes() -> tuple[int, int]:
    rx = 0
    tx = 0
    for iface in glob.glob("/sys/class/net/*"):
        if os.path.basename(iface) == "lo":
            continue
        try:
            if Path(iface, "operstate").read_text(encoding="ascii").strip() != "up":
                continue
            rx += int(Path(iface, "statistics/rx_bytes").read_text(encoding="ascii"))
            tx += int(Path(iface, "statistics/tx_bytes").read_text(encoding="ascii"))
        except (OSError, ValueError):
            continue
    return rx, tx


def _run(command: list[str], timeout: float = 2.0) -> str:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _temperature() -> float | None:
    output = _run(["/usr/bin/vcgencmd", "measure_temp"])
    if output.startswith("temp="):
        try:
            return float(output.split("=", 1)[1].split("'", 1)[0])
        except (ValueError, IndexError):
            pass
    for path in glob.glob("/sys/class/hwmon/hwmon*/temp*_input"):
        try:
            value = int(Path(path).read_text(encoding="ascii")) / 1000
            if 0 < value < 120:
                return round(value, 1)
        except (OSError, ValueError):
            continue
    return None


def _throttle() -> dict[str, object]:
    output = _run(["/usr/bin/vcgencmd", "get_throttled"])
    code = output.split("=", 1)[1] if "=" in output else "unknown"
    try:
        ok = int(code, 16) == 0
    except ValueError:
        ok = False
    return {"ok": ok, "code": code}


def _frequency_mhz() -> float | None:
    candidates = [
        "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq",
        "/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq",
    ]
    for path in candidates:
        try:
            return round(int(Path(path).read_text(encoding="ascii")) / 1000, 1)
        except (OSError, ValueError):
            continue
    return None


def _os_name() -> str:
    try:
        values = {}
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value.strip().strip('"')
        return f"{values.get('NAME', 'Linux')} {values.get('VERSION_ID', '')}".strip()
    except OSError:
        return "Linux"


def _hardware_model() -> str:
    try:
        return Path("/proc/device-tree/model").read_bytes().rstrip(b"\0").decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return "Raspberry Pi"


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition(":")
            if separator and key.strip().lower() == "model name":
                return value.strip()
    except OSError:
        pass
    for line in _run(["/usr/bin/lscpu"]).splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "Model name":
            return value.strip()
    return "ARM processor"


def _timezone_name() -> str:
    try:
        return Path("/etc/timezone").read_text(encoding="utf-8").strip() or "UTC"
    except OSError:
        return "UTC"


class Telemetry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, object] = {}
        self._docker = {"running": 0, "total": 0, "healthy": 0}
        self._docker_checked = 0.0
        self._failed_units = 0
        self._system_checked = 0.0
        self._platform = _os_name()
        self._arch = platform.machine()
        self._host = platform.node()
        self._model = _hardware_model()
        self._cpu_model = _cpu_model()
        self._kernel = platform.release()
        self._timezone = _timezone_name()
        self._docker_version = _run(["/usr/bin/docker", "version", "--format", "{{.Server.Version}}"], timeout=3.0)
        uptime = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
        self._booted_at = datetime.fromtimestamp(time.time() - uptime, timezone.utc).isoformat()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="telemetry", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return dict(self._data)

    def _docker_status(self, now: float) -> dict[str, int]:
        if now - self._docker_checked < 15:
            return self._docker
        statuses = _run(["/usr/bin/docker", "ps", "--format", "{{.Status}}"], timeout=3.0).splitlines()
        total_output = _run(["/usr/bin/docker", "ps", "-aq"], timeout=3.0)
        self._docker = {
            "running": len(statuses),
            "total": len(total_output.splitlines()) if total_output else 0,
            "healthy": sum("(healthy)" in status for status in statuses),
        }
        self._docker_checked = now
        return self._docker

    def _failed_unit_count(self, now: float) -> int:
        if now - self._system_checked < 30:
            return self._failed_units
        output = _run([
            "/usr/bin/systemctl", "list-units", "--state=failed", "--no-legend", "--plain", "--no-pager"
        ], timeout=3.0)
        self._failed_units = len(output.splitlines()) if output else 0
        self._system_checked = now
        return self._failed_units

    def _loop(self) -> None:
        previous_cpu = _cpu_times()
        previous_network = _network_bytes()
        previous_time = time.monotonic()
        self._stop.wait(0.8)
        while not self._stop.is_set():
            now_mono = time.monotonic()
            current_cpu = _cpu_times()
            cpu_percent = _cpu_percent(previous_cpu.get("cpu"), current_cpu.get("cpu", (0, 0))) or 0.0
            per_core = [
                {"id": int(name[3:]), "percent": _cpu_percent(previous_cpu.get(name), current_cpu[name])}
                for name in sorted((name for name in current_cpu if name != "cpu"), key=lambda name: int(name[3:]))
            ]
            previous_cpu = current_cpu

            current_network = _network_bytes()
            seconds = max(0.1, now_mono - previous_time)
            rx_bps = max(0, round((current_network[0] - previous_network[0]) / seconds))
            tx_bps = max(0, round((current_network[1] - previous_network[1]) / seconds))
            previous_network = current_network
            previous_time = now_mono

            memory = _read_key_values("/proc/meminfo")
            mem_total = memory.get("MemTotal", 0)
            mem_available = memory.get("MemAvailable", 0)
            mem_used = max(0, mem_total - mem_available)
            swap_total = memory.get("SwapTotal", 0)
            swap_free = memory.get("SwapFree", 0)
            disk = shutil.disk_usage("/")
            temp = _temperature()
            throttle = _throttle()
            load1, load5, load15 = os.getloadavg()
            now = time.time()
            docker = self._docker_status(now)
            failed_units = self._failed_unit_count(now)

            warnings = [
                cpu_percent >= 90,
                mem_total > 0 and mem_used / mem_total >= 0.9,
                disk.total > 0 and disk.used / disk.total >= 0.9,
                temp is not None and temp >= 80,
                not throttle["ok"],
            ]

            data = {
                "status": "attention" if any(warnings) else "healthy",
                "host": self._host,
                "platform": self._platform,
                "arch": self._arch,
                "machine": {
                    "model": self._model,
                    "cpu_model": self._cpu_model,
                    "kernel": self._kernel,
                    "booted_at": self._booted_at,
                    "timezone": self._timezone,
                    "docker_version": self._docker_version,
                    "failed_units": failed_units,
                },
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "uptime_seconds": round(float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])),
                "temperature_c": temp,
                "throttle": throttle,
                "cpu": {
                    "percent": cpu_percent,
                    "cores": len(per_core) or os.cpu_count() or 1,
                    "per_core": per_core,
                    "load1": round(load1, 2),
                    "load5": round(load5, 2),
                    "load15": round(load15, 2),
                    "frequency_mhz": _frequency_mhz(),
                },
                "memory": {
                    "total": mem_total,
                    "used": mem_used,
                    "available": mem_available,
                    "percent": round(100 * mem_used / mem_total, 1) if mem_total else 0,
                    "swap_total": swap_total,
                    "swap_used": max(0, swap_total - swap_free),
                },
                "disk": {
                    "total": disk.total,
                    "used": disk.used,
                    "free": disk.free,
                    "percent": round(100 * disk.used / disk.total, 1) if disk.total else 0,
                },
                "network": {"rx_bps": rx_bps, "tx_bps": tx_bps},
                "docker": docker,
            }
            with self._lock:
                self._data = data
            self._stop.wait(SAMPLE_INTERVAL)


TELEMETRY = Telemetry()


def _fan_request(payload: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect("/run/pi-fan/control.sock")
        client.sendall(json.dumps(payload).encode() + b"\n")
        with client.makefile("rb") as stream:
            raw = stream.readline(4097)
        if len(raw) > 4096:
            raise ValueError("Invalid controller response")
        result = json.loads(raw)
        if not result.get("ok"):
            raise ValueError("Controller rejected request")
        return result["fan"]


def _vpn_request(payload: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect("/run/pi-vpn/control.sock")
        client.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
        with client.makefile("rb") as stream:
            raw = stream.readline(32769)
        if len(raw) > 32768:
            raise ValueError("Invalid controller response")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Invalid controller response")
        return result


def _admin_request(payload: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect("/run/pi-admin/control.sock")
        client.sendall(json.dumps(payload, ensure_ascii=False).encode() + b"\n")
        with client.makefile("rb") as stream:
            raw = stream.readline(16385)
        if len(raw) > 16384:
            raise ValueError("Invalid admin response")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Invalid admin response")
        return result


class Handler(BaseHTTPRequestHandler):
    server_version = "PiDashboard/1.0"
    sys_version = ""

    def _send(self, status: int, content_type: str, body: bytes, cache: str, extra_headers=None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self) -> None:
        self.do_GET()

    def _json(self, status: int, value: dict, extra_headers=None) -> None:
        self._send(status, "application/json; charset=utf-8", json.dumps(value, ensure_ascii=False).encode(), "no-store", extra_headers)

    def _session(self):
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            return cookies[SESSION_COOKIE].value if SESSION_COOKIE in cookies else ""
        except CookieError:
            return ""

    def _origin_allowed(self):
        origin = self.headers.get("Origin")
        allowed = {"https://api.pi-hardbox.ru", "http://127.0.0.1:8080", "http://localhost:8080"}
        if (self.command == "POST" and origin not in allowed) or (origin and origin not in allowed):
            self._json(403, {"error": "Недопустимый источник запроса"})
            return False
        return True

    def _authorized(self) -> bool:
        if not self._origin_allowed():
            return False
        try:
            authorized = _admin_request({"operation": "authorize", "session": self._session()})
        except (OSError, ValueError):
            self._json(503, {"error": "Служба входа временно недоступна"})
            return False
        if not authorized.get("authenticated"):
            self._json(401, {"error": "Войдите с паролем sudo пользователя admin"})
            return False
        return True

    def do_POST(self) -> None:
        self.close_connection = True
        path = urlsplit(self.path).path
        auth_path = path in {"/api/auth/login", "/api/auth/logout"}
        if path not in {"/api/fan", "/api/vpn/config", "/api/vpn/rules", "/api/vpn/rules/refresh", "/api/admin/reboot"} and not auth_path:
            self._json(404, {"error": "Неизвестный адрес"})
            return
        if not (self._origin_allowed() if auth_path else self._authorized()):
            return
        if self.headers.get_content_type() != "application/json":
            self._json(415, {"error": "Ожидается JSON"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        limit = 12288 if path.startswith("/api/vpn/") else 2048 if path == "/api/auth/login" else 512
        if not 0 < size <= limit or self.headers.get("Transfer-Encoding"):
            self._json(400, {"error": "Недопустимый размер запроса"})
            return
        self.connection.settimeout(3)
        try:
            data = json.loads(self.rfile.read(size))
            if auth_path or path == "/api/admin/reboot":
                if not isinstance(data, dict):
                    raise ValueError()
                if path.endswith("/login"):
                    if not isinstance(data.get("password"), str) or not data["password"] or len(data["password"].encode()) > 512:
                        self._json(400, {"error": "Введите пароль пользователя admin"})
                        return
                    payload = {"operation": "login", "password": data["password"]}
                else:
                    payload = {"operation": "logout" if auth_path else "reboot", "session": self._session()}
                    if not auth_path:
                        payload.update(confirm=data.get("confirm"), request_id=data.get("request_id"))
                try:
                    result = _admin_request(payload)
                except (OSError, ValueError):
                    self._json(503, {"error": "Нет подтверждения службы управления. Проверьте состояние перед повтором."})
                    return
                if path.endswith("/logout"):
                    self._json(200, {"authenticated": False}, {"Set-Cookie": SESSION_COOKIE + "=; Path=/; Max-Age=0; Secure; HttpOnly; SameSite=Strict"})
                elif not result.get("ok"):
                    code = {"unauthorized": 401, "limited": 429, "invalid": 400}.get(result.get("code"), 503)
                    self._json(code, {"error": result.get("error", "Служба управления недоступна"), "uncertain": result.get("code") == "uncertain"}, {"Retry-After": "60"} if code == 429 else None)
                elif path.endswith("/login"):
                    self._json(200, {"authenticated": True, "expires_in": result["expires_in"]},
                               {"Set-Cookie": SESSION_COOKIE + "=" + result["session"] + "; Path=/; Max-Age=600; Secure; HttpOnly; SameSite=Strict"})
                else:
                    self._json(202, {"reboot": result["reboot"]})
                return
            if path in {"/api/vpn/rules", "/api/vpn/rules/refresh"}:
                if not isinstance(data, dict) or not isinstance(data.get("request_id"), str):
                    raise ValueError()
                payload = {"operation": "set-rules" if path == "/api/vpn/rules" else "refresh-rules", "request_id": data["request_id"]}
                if path == "/api/vpn/rules":
                    payload.update(direct=data.get("direct"), vpn=data.get("vpn"), revision=data.get("revision"))
                try:
                    result = _vpn_request(payload)
                except (OSError, ValueError):
                    self._json(503, {"error": "Контроллер VPN недоступен. Проверьте статус перед повтором."})
                    return
                if not result.get("ok"):
                    self._json(409 if result.get("code") in {"busy", "conflict"} else 400 if result.get("code") == "invalid" else 503,
                               {"error": result.get("error", "Не удалось принять правила")})
                    return
                self._json(202, {"job": result["job"]})
                return
            if path == "/api/vpn/config":
                if not isinstance(data, dict) or not isinstance(data.get("uri"), str) or not isinstance(data.get("request_id"), str):
                    raise ValueError()
                try:
                    result = _vpn_request({"operation": "replace", "uri": data["uri"], "request_id": data["request_id"]})
                except (OSError, ValueError):
                    self._json(503, {"error": "Контроллер VPN недоступен. Проверьте статус перед повтором."})
                    return
                if not result.get("ok"):
                    code = 409 if result.get("code") == "busy" else 400 if result.get("code") == "invalid" else 503
                    self._json(code, {"error": result.get("error", "Не удалось принять ключ")})
                    return
                self._json(202, {"job": result["job"]})
                return
            if not isinstance(data, dict) or data.get("mode") not in ("auto", "manual"):
                raise ValueError()
            if data["mode"] == "manual" and (type(data.get("percent")) is not int or not 20 <= data["percent"] <= 100):
                raise ValueError()
        except (ValueError, UnicodeDecodeError, OSError):
            self._json(400, {"error": "Проверьте данные и идентификатор операции" if path.startswith("/api/vpn/") else "Укажите режим auto/manual и целую скорость от 20 до 100%"})
            return
        try:
            fan = _fan_request({"operation": "set", "mode": data["mode"], "percent": data.get("percent")})
        except (OSError, ValueError, KeyError):
            self._json(503, {"error": "Контроллер вентилятора недоступен. Проверьте текущий режим."})
            return
        if not fan.get("available"):
            self._json(503, {"error": "Не удалось подтвердить скорость. Включён защитный режим."})
            return
        self._json(200, {"fan": fan})

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path in PUBLIC_ASSETS:
            filename, mime = PUBLIC_ASSETS[path]
            try:
                body = (ASSET_DIR / filename).read_bytes()
            except OSError:
                self._send(404, "text/plain; charset=utf-8", b"asset unavailable\n", "no-store")
                return
            self._send(200, mime, body, "public, max-age=86400")
        elif path in {"/", "/vpn", "/vpn/", "/manage", "/manage/"}:
            html = HTML
            if path.startswith("/vpn"):
                html = html.replace('data-page="server"', 'data-page="vpn"', 1).replace("<title>PI Hardbox — состояние сервера</title>", "<title>VPN и Wi-Fi — PI Hardbox</title>")
                html = html.replace('<span class="live-rate">/ 3 сек</span>', '<span class="live-rate">/ 5 сек</span>')
            elif path.startswith("/manage"):
                html = html.replace('data-page="server"', 'data-page="manage"', 1).replace("<title>PI Hardbox — состояние сервера</title>", "<title>Управление сервером — PI Hardbox</title>")
            canonical = "/vpn" if path.startswith("/vpn") else "/manage" if path.startswith("/manage") else "/"
            html = html.replace("__PAGE_URL__", "https://api.pi-hardbox.ru" + canonical)
            if canonical != "/":
                title = "VPN и Wi-Fi — PI Hardbox" if canonical == "/vpn" else "Управление сервером — PI Hardbox"
                html = html.replace('content="PI Hardbox — состояние сервера"', 'content="' + title + '"')
            self._send(200, "text/html; charset=utf-8", html.encode(), "public, max-age=60")
        elif path == "/style.css":
            self._send(200, "text/css; charset=utf-8", CSS.encode(), "public, max-age=300")
        elif path == "/app.js":
            self._send(200, "text/javascript; charset=utf-8", JS.encode(), "public, max-age=300")
        elif path == "/api/projects":
            self._json(200, PROJECTS.snapshot())
        elif path in {"/api/auth", "/api/manage"}:
            try:
                if path == "/api/auth":
                    result = _admin_request({"operation": "authorize", "session": self._session()})
                    self._json(200, {"authenticated": bool(result.get("authenticated")), "reboot": result.get("reboot")})
                else:
                    result = _admin_request({"operation": "status"})
                    self._json(200, {"available": True, "services": result.get("services", [])})
            except (OSError, ValueError):
                self._json(503, {"error": "Служба управления временно недоступна"})
        elif path == "/api/vpn":
            try:
                result = _vpn_request({"operation": "status"})
                self._json(200, result["vpn"])
            except (OSError, ValueError, KeyError):
                self._json(503, {"available": False, "error": "Контроллер VPN недоступен"})
        elif path == "/api/vpn/operation":
            if not self._authorized():
                return
            try:
                request_id = parse_qs(urlsplit(self.path).query, max_num_fields=4).get("request_id", [None])[0]
                result = _vpn_request({"operation": "latest", "request_id": request_id})
                self._json(200, {"job": result["job"]})
            except (OSError, ValueError, KeyError):
                self._json(503, {"error": "Не удалось получить результат операции"})
        elif path == "/api/vpn/rules":
            if not self._authorized():
                return
            try:
                result = _vpn_request({"operation": "rules"})
                self._json(200, {"rules": result["rules"]})
            except (OSError, ValueError, KeyError):
                self._json(503, {"error": "Не удалось загрузить исключения"})
        elif path == "/api/status":
            data = TELEMETRY.snapshot()
            if data:
                try:
                    data["fan"] = _fan_request({"operation": "status"})
                except (OSError, ValueError, KeyError):
                    data["fan"] = {"available": False}
                self._send(200, "application/json; charset=utf-8", json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(), "no-store")
            else:
                self._send(503, "application/json; charset=utf-8", b'{"status":"starting"}', "no-store")
        elif path == "/health":
            self._send(200, "text/plain; charset=utf-8", b"ok\n", "no-store")
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found\n", "no-store")

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    TELEMETRY.start()
    PROJECTS.start()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    print(f"Pi dashboard listening on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

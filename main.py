import os
import time
import re
import requests
import smtplib
from email.mime.text import MIMEText
from imap_tools import MailBox, AND
from datetime import datetime

# --- НАСТРОЙКИ ---
CONFIG = {
    "imap_server": "imap.gmail.com",
    "email_user": "smithjohn01042000@gmail.com",
    "email_pass": "nghy letu udzx hbdc",
    "yandex_token": "y0__wgBEPSB7IQCGNuWAyDTpcOiF_PK9W6CqB0N1qJP3wxXsAgcvmWm",
    "target_notification": "idolon666@gmail.com",
    "sender_filter": "idolon666@gmail.com",
    "sleep_time": 60,
    "github_token": "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", # Рекомендую добавить в переменные окружения
    "gist_id": "e9b2b65cab1cf8581fe3b33ff47681d6"
}

def update_heartbeat():
    """Обновляет Gist для системы мониторинга"""
    if not CONFIG["github_token"]:
        return
    url = f"https://api.github.com/gists/{CONFIG['gist_id']}"
    headers = {"Authorization": f"token {CONFIG['github_token']}"}
    data = {
        "description": "Heartbeat for Yandex Disk Downloader",
        "files": {"heartbeat.txt": {"content": f"Last run: {datetime.now().isoformat()}"}}
    }
    try:
        requests.patch(url, json=data, headers=headers, timeout=10)
    except:
        pass

def clean_name(name):
    """
    Обрезает строку до первого непечатного символа или запрещенного знака.
    ПРОБЕЛЫ ТЕПЕРЬ РАЗРЕШЕНЫ.
    """
    if not name:
        return "unnamed"
    
    # Из списка запрещенных убран пробел: / \ : * ? " < > | '
    match = re.search(r'[\x00-\x1F\x7F/\\:\*\?"<>|\']', name)
    if match:
        name = name[:match.start()]
    
    return name.strip() or "unnamed"

def create_yandex_folder(folder_name):
    """Создает папку на Яндекс.Диске, если её нет"""
    headers = {"Authorization": f"OAuth {CONFIG['yandex_token']}"}
    clean_folder = clean_name(folder_name)
    requests.put(f"https://cloud-api.yandex.net/v1/disk/resources?path={clean_folder}", headers=headers)
    return clean_folder

def get_file_size_mb(path):
    headers = {"Authorization": f"OAuth {CONFIG['yandex_token']}"}
    res = requests.get(f"https://cloud-api.yandex.net/v1/disk/resources?path={path}", headers=headers)
    if res.status_code == 200:
        size_bytes = res.json().get('size', 0)
        return round(size_bytes / (1024 * 1024), 2)
    return 0

def send_final_notification(filename, size_mb):
    body = (
        f"✅ Загрузка на Яндекс.Диск завершена!\n\n"
        f"📁 Файл: {filename}\n"
        f"⚖️ Размер: {size_mb} MB\n"
    )
    msg = MIMEText(body)
    msg['Subject'] = f"Завершено: {filename}"
    msg['From'] = CONFIG["email_user"]
    msg['To'] = CONFIG["target_notification"]
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(CONFIG["email_user"], CONFIG["email_pass"])
            server.send_message(msg)
    except: pass

def parse_content(text):
    text = re.sub('<[^<]+?>', '', text).replace('\r', '').strip()
    url_match = re.search(r'https?://[^\s]+', text)
    if not url_match:
        return None, None
    url = url_match.group(0).strip()
    
    series_match = re.search(r'Смотреть (.+?) Сезон (\d+) - Эпизод (\d+)', text)
    if series_match:
        title, s_num, e_num = series_match.groups()
        folder = create_yandex_folder(title.strip())
        filename = f"{int(s_num):02d}-{int(e_num):02d}.mp4"
        return url, f"{folder}/{filename}"

    movie_match = re.search(r'Смотреть (.+?) (?:Дубляж|1080p|720p|\d+p)', text)
    if movie_match:
        title = clean_name(movie_match.group(1).strip())
        return url, f"{title}.mp4"

    other_match = re.search(rf'{re.escape(url)}\s+(.+)', text)
    if other_match:
        filename = clean_name(other_match.group(1).strip())
        return url, filename
    
    raw_name = url.split('/')[-1]
    return url, clean_name(raw_name)

def upload_via_yandex_async(url, yandex_path):
    headers = {"Authorization": f"OAuth {CONFIG['yandex_token']}"}
    params = {"url": url, "path": f"/{yandex_path}", "overwrite": "true"}
    
    try:
        res = requests.post("https://cloud-api.yandex.net/v1/disk/resources/upload", params=params, headers=headers)
        if res.status_code not in [201, 202]: return False
        
        status_url = res.json().get('href')
        while True:
            status_res = requests.get(status_url, headers=headers)
            status = status_res.json().get('status', 'failed')
            if status == 'success': return True
            elif status == 'failed': return False
            time.sleep(20)
    except: return False

# --- ЦИКЛ ---
print(f"--- Скрипт запущен ---")
last_heartbeat = 0

while True:
    # Обновление Heartbeat раз в 1 минут
    if time.time() - last_heartbeat > 60:
        update_heartbeat()
        last_heartbeat = time.time()

    try:
        with MailBox(CONFIG["imap_server"]).login(CONFIG["email_user"], CONFIG["email_pass"]) as mailbox:
            msgs = list(mailbox.fetch(AND(from_=CONFIG["sender_filter"], seen=False)))
            for msg in msgs:
                content = msg.text or msg.html or ""
                f_url, f_path = parse_content(content)
                
                if f_url and f_path:
                    if upload_via_yandex_async(f_url, f_path):
                        size = get_file_size_mb(f_path)
                        send_final_notification(f_path, size)
                        mailbox.flag(msg.uid, ['SEEN'], True)
                    else:
                        print(f"Ошибка загрузки: {f_path}")
                else:
                    mailbox.flag(msg.uid, ['SEEN'], True)
    except Exception as e:
        print(f"Ошибка: {e}")
    
    time.sleep(CONFIG["sleep_time"])
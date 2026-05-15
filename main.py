import os
import time
import re
import requests
import smtplib
from email.mime.text import MIMEText
from imap_tools import MailBox, AND
from datetime import datetime

# Чтение настроек из Secrets (переменных окружения)
CONFIG = {
    "imap_server": "imap.gmail.com",
    "email_user": os.getenv("EMAIL_USER"),
    "email_pass": os.getenv("EMAIL_PASS"),
    "yandex_token": os.getenv("YANDEX_TOKEN"),
    "target_notification": os.getenv("TARGET_NOTIFICATION"),
    "sender_filter": os.getenv("SENDER_FILTER", os.getenv("TARGET_NOTIFICATION")),
}

def clean_name(name):
    if not name:
        return "unnamed"
    match = re.search(r'[\x00-\x1F\x7F/\\:\*\?"<>|\']', name)
    if match:
        name = name[:match.start()]
    return name.strip() or "unnamed"

def create_yandex_folder(folder_name):
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
    except Exception as e:
        print(f"Ошибка отправки уведомления: {e}")

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
            status_data = status_res.json()
            status = status_data.get('status')
            if status == 'success': return True
            if status == 'failed': return False
            time.sleep(20)
    except Exception as e:
        print(f"Ошибка API Яндекса: {e}")
        return False

def main():
    print(f"--- Запуск проверки почты: {datetime.now().isoformat()} ---")
    try:
        with MailBox(CONFIG["imap_server"]).login(CONFIG["email_user"], CONFIG["email_pass"]) as mailbox:
            # Ищем только непрочитанные письма от нужного отправителя
            messages = list(mailbox.fetch(AND(from_=CONFIG["sender_filter"], seen=False)))
            
            if not messages:
                print("Новых писем не обнаружено.")
                return

            for msg in messages:
                content = msg.text or msg.html or ""
                f_url, f_path = parse_content(content)
                
                if f_url and f_path:
                    print(f"Найдена ссылка. Загрузка {f_path}...")
                    if upload_via_yandex_async(f_url, f_path):
                        size = get_file_size_mb(f_path)
                        send_final_notification(f_path, size)
                        print(f"Успешно загружено: {f_path}")
                
                # Помечаем прочитанным в любом случае, чтобы не обрабатывать повторно
                mailbox.flag(msg.uid, ['SEEN'], True)
                
    except Exception as e:
        print(f"Критическая ошибка: {e}")

if __name__ == "__main__":
    main()

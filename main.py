import os
import time
import re
import requests
import smtplib
from email.mime.text import MIMEText
from imap_tools import MailBox, AND
from datetime import datetime

# Чтение настроек из Secrets
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
    # Очистка текста от HTML и лишних переносов
    text = re.sub('<[^<]+?>', '', text).replace('\r', '').strip()
    
    # Поиск URL
    url_match = re.search(r'https?://[^\s]+', text)
    if not url_match:
        return None, None, None, None
    url = url_match.group(0).strip()
    
    # Поиск Login: и Pass:
    login_match = re.search(r'Login:\s*(\S+)', text)
    pass_match = re.search(r'Pass:\s*(\S+)', text)
    
    login = login_match.group(1) if login_match else None
    password = pass_match.group(1) if pass_match else None

    # Определение пути (сериал или фильм)
    series_match = re.search(r'Смотреть (.+?) Сезон (\d+) - Эпизод (\d+)', text)
    if series_match:
        title, s_num, e_num = series_match.groups()
        folder = create_yandex_folder(title.strip())
        filename = f"{int(s_num):02d}-{int(e_num):02d}.mp4"
        return url, f"{folder}/{filename}", login, password

    movie_match = re.search(r'Смотреть (.+?) (?:Дубляж|1080p|720p|\d+p)', text)
    if movie_match:
        title = clean_name(movie_match.group(1).strip())
        return url, f"{title}.mp4", login, password
    
    raw_name = url.split('/')[-1]
    return url, clean_name(raw_name), login, password

def upload_via_yandex_async(url, yandex_path, login=None, password=None):
    headers = {"Authorization": f"OAuth {CONFIG['yandex_token']}"}
    params = {
        "url": url, 
        "path": f"/{yandex_path}", 
        "overwrite": "true"
    }
    
    # Добавляем данные авторизации, если они есть
    if login: params["login"] = login
    if password: params["password"] = password
    
    try:
        res = requests.post("https://cloud-api.yandex.net/v1/disk/resources/upload", params=params, headers=headers)
        if res.status_code not in [201, 202]:
            print(f"Ошибка инициализации загрузки: {res.text}")
            return False
        
        status_url = res.json().get('href')
        print(f"Загрузка начата (Auth: {'Да' if login else 'Нет'}). Ожидаем завершения...")

        while True:
            status_res = requests.get(status_url, headers=headers)
            status_data = status_res.json()
            status = status_data.get('status')

            if status == 'success':
                print(f"Файл {yandex_path} успешно загружен.")
                return True
            elif status == 'failed':
                print(f"Яндекс сообщил об ошибке загрузки {yandex_path}. Проверьте доступ или лимиты.")
                return False
            elif status in ['in-progress', 'waiting']:
                time.sleep(30)
            else:
                print(f"Статус: {status}. Ждем...")
                time.sleep(30)
                
    except Exception as e:
        print(f"Ошибка в процессе ожидания загрузки: {e}")
        return False

def main():
    print(f"--- Старт сессии: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    
    try:
        with MailBox(CONFIG["imap_server"]).login(CONFIG["email_user"], CONFIG["email_pass"]) as mailbox:
            messages = list(mailbox.fetch(AND(from_=CONFIG["sender_filter"], seen=False)))
            
            if not messages:
                print("Новых заданий в почте нет.")
                return

            print(f"Найдено писем для обработки: {len(messages)}")

            for msg in messages:
                content = msg.text or msg.html or ""
                f_url, f_path, f_login, f_pass = parse_content(content)
                
                if f_url and f_path:
                    print(f"Обработка: {f_path}")
                    success = upload_via_yandex_async(f_url, f_path, f_login, f_pass)
                    
                    if success:
                        size = get_file_size_mb(f_path)
                        send_final_notification(f_path, size)
                    else:
                        print(f"Не удалось загрузить файл из письма {msg.uid}")
                
                mailbox.flag(msg.uid, ['SEEN'], True)
                
    except Exception as e:
        print(f"Ошибка выполнения: {e}")
    
    print(f"--- Сессия завершена ---")

if __name__ == "__main__":
    main()

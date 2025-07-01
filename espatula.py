import os
import json
import time
import hashlib
import queue
import requests
from urllib.parse import urlparse, unquote
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

CONFIG_PATH = "config.json"
FAILED_LOG_PATH = "failed_downloads.log"
url_queue = queue.Queue()
downloaded_urls = set()

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)

def setup_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)

def md5(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def login(driver, config):
    while True:
        try:
            print("[→] Logging in...")
            driver.get(config["login_url"])
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "navbar_loginform")))

            driver.find_element(By.ID, "navbar_username").clear()
            driver.find_element(By.ID, "navbar_username").send_keys(config["username"])
            driver.find_element(By.ID, "navbar_password_hint").click()
            driver.find_element(By.ID, "navbar_password").send_keys(config["password"])

            hashed_pw = md5(config["password"])
            driver.execute_script(f'document.getElementsByName("vb_login_md5password")[0].value = "{hashed_pw}";')
            driver.execute_script(f'document.getElementsByName("vb_login_md5password_utf")[0].value = "{hashed_pw}";')
            driver.find_element(By.ID, "navbar_loginform").submit()
            time.sleep(3)

            if config["username"] in driver.page_source or "logout.php" in driver.page_source:
                print("[✓] Logged in successfully.")
                return
            else:
                print("[!] Login failed. Retrying in 5 seconds...")
        except Exception as e:
            print(f"[!] Login error: {e}. Retrying in 5 seconds...")

        time.sleep(5)

def check_login(driver, config):
    return config["username"] in driver.page_source or "logout.php" in driver.page_source

def sanitize_filename(url):
    path = urlparse(url).path
    return os.path.basename(unquote(path)).split("?")[0]

def get_folder_name_from_url(url):
    parsed = urlparse(url)
    netloc = parsed.netloc
    path = parsed.path.strip("/").replace("/", "_")
    return f"{netloc}-{path}" if path else netloc

def preload_downloaded(download_dir):
    try:
        for f in os.listdir(download_dir):
            downloaded_urls.add(f)
    except FileNotFoundError:
        os.makedirs(download_dir, exist_ok=True)

def download_sequentially(download_dir):
    while not url_queue.empty():
        url = url_queue.get()
        filename = sanitize_filename(url)
        out_path = os.path.join(download_dir, filename)

        if filename in downloaded_urls:
            print(f"[≡] Skipped (already downloaded): {filename}")
            continue

        downloaded_urls.add(filename)

        try:
            print(f"[↓] Downloading: {url}")
            r = requests.get(url, stream=True, timeout=30)
            if r.status_code == 200:
                with open(out_path, 'wb') as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
            else:
                raise Exception(f"HTTP {r.status_code}")
        except Exception as e:
            print(f"[!] Failed: {url}  Reason: {e}")
            with open(FAILED_LOG_PATH, "a", encoding="utf-8") as log:
                log.write(f"{url}  # {e}\n")

def scraping_loop(driver, config, download_dir):
    current_page = config["last_page"]
    selector = config.get("content_selector", "blockquote.postcontent.restore")

    while True:
        page_url = config["page_url_pattern"].format(n=current_page)
        print(f"[→] Scraping page: {page_url}")

        try:
            driver.get(page_url)

            if not check_login(driver, config):
                print("[!] Session expired. Re-logging in...")
                login(driver, config)
                driver.get(page_url)

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )

            containers = driver.find_elements(By.CSS_SELECTOR, selector)
            if not containers:
                print(f"[!] No containers matched {selector} on page {current_page}")
                break

            new_links = 0
            for container in containers:
                for tag in container.find_elements(By.TAG_NAME, "video") + \
                              container.find_elements(By.TAG_NAME, "img") + \
                              container.find_elements(By.TAG_NAME, "source"):
                    src = tag.get_attribute("src")
                    if src and src.startswith("http"):
                        url_queue.put(src)
                        new_links += 1

            print(f"[+] Found {new_links} media links on page {current_page}")
            current_page += 1
            config["last_page"] = current_page
            save_config(config)

            download_sequentially(download_dir)
            time.sleep(2)

        except (TimeoutException, WebDriverException) as e:
            print(f"[!] Error during scraping page {current_page}: {e}. Retrying...")
            time.sleep(5)
            continue
        except Exception as e:
            print(f"[!] Unexpected error: {e}. Retrying in 10 seconds...")
            time.sleep(10)

def main():
    config = load_config()
    folder_name = get_folder_name_from_url(config["page_url_pattern"])
    download_dir = os.path.join(config["download_dir"], folder_name)
    preload_downloaded(download_dir)

    while True:
        try:
            driver = setup_driver()
            login(driver, config)
            scraping_loop(driver, config, download_dir)
            print("[✓] Finished scraping and downloading. Restarting loop...")
        except Exception as e:
            print(f"[!] Fatal error: {e}. Restarting everything in 10 seconds...")
        finally:
            try:
                driver.quit()
            except:
                pass
        time.sleep(10)

if __name__ == "__main__":
    main()

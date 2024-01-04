# With this script you can download millions of files from a onedrive folder.
# This script was copied from https://github.com/O365/python-o365/blame/master/examples/onedrive_example.py
#
# Original Authors:
#   * https://github.com/lamductan
#   * https://github.com/eshifflett
# 
# Modifications By:
#   * https://github.com/iamtalhaasghar
#
# 2024-01-04

import os
import sqlite3, pymysql
import argparse
from O365 import Account
from O365 import FileSystemTokenBackend
import logging
from dotenv import load_dotenv
import multiprocessing, threading
from concurrent.futures import ThreadPoolExecutor
import time
import requests

load_dotenv()

client_id = os.getenv('CLIENT_ID')  # Your client_id
client_secret = os.getenv('CLIENT_SECRET')  # Your client_secret, create an (id, secret) at https://apps.dev.microsoft.com
scopes = ['basic', 'https://graph.microsoft.com/Files.ReadWrite.All']
CHUNK_SIZE = 1024 * 1024 * 5
logging.basicConfig(filename='log.txt', level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
logging.getLogger().addHandler(console_handler)

connection = dict(host="localhost",
            user=os.getenv('DB_USER'),
            password=os.getenv('DB_PASS'),
            database=os.getenv('DB_NAME'), cursorclass=pymysql.cursors.DictCursor)

class O365Account():
    def __init__(self, client_id=client_id,
                 client_secret=client_secret, scopes=scopes):
        self.client_id = client_id
        self.client_secret = client_secret
        self.account = Account(credentials=(client_id, client_secret))
        self.token_backend = FileSystemTokenBackend(token_path='.', token_filename='o365_token.txt')
        #self.authenticate(scopes)
        self.storage = self.account.storage()
        self.drives = self.storage.get_drives()
        self.my_drive = self.storage.get_default_drive()  # or get_drive('drive-id')
        self.root_folder = self.my_drive.get_root_folder()

    def authenticate(self, scopes=scopes):
        try:
            # Attempt to load a stored token
            result = self.account.authenticate(scopes=scopes, token_backend=self.token_backend)
        except FileNotFoundError as e:
            # If no stored token is found, prompt for consent
            result = self.account.authenticate(scopes=scopes, token_backend=self.token_backend)

        if not result:
            # Handle authentication failure (e.g., user did not grant consent)
            print("Authentication failed. Please check your credentials and try again.")
            exit()

    def get_drive(self):
        return self.my_drive

    def get_root_folder(self):
        return self.root_folder

    def get_metadata(self):
        folder = self.my_drive.get_item_by_path('/tweets/')

        conn = sqlite3.connect('tweets.db')
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM files;")
        skip_count = cursor.fetchone()[0]
       
        all_items = folder.get_items()
        file_count = 0
        for item in all_items:
            if item.is_file:
                try:
                    file_count += 1
                    
                    # save changes to db after a while
                    if file_count % 1000 == 0:
                        logging.info(f'saving changes {file_count}')
                        conn.commit()

                    # skip alread saved files
                    if file_count < skip_count:
                        continue
                    logging.debug(f"{file_count}. {item.name}")
                    # Use parameterized query to prevent SQL injection
                    cursor.execute('''
                          INSERT INTO files (name) VALUES (?);
                    ''', (item.name,))                    
                except sqlite3.IntegrityError:
                    pass                

        conn.commit()
        conn.close()
        

    # Function to fetch 1k files from the database
    def fetch_files(self, batch_size=1000):
        # Replace this with your actual database connection details
        conn = pymysql.connect(
            **connection
        )
        cursor = conn.cursor()
        logging.info(f"fetching {batch_size} files")
        # Fetch files that have not been downloaded yet
        select_query = "SELECT `id`, `name` FROM `files` WHERE `downloaded`=%s limit %s"
        cursor.execute(select_query, ('0',batch_size))
        files_to_download = cursor.fetchall()

        conn.close()

        return files_to_download

    def download_file(self, fileinfo):
        filename = fileinfo['name']
        try:
            logging.info(f"{threading.current_thread().name} downloading {filename}")
            self.my_drive.get_item_by_path('/tweets/' + filename).download('/mnt/data/o365/tweets/')
            logging.info(f"{threading.current_thread().name} downloaded {filename}")
            return (fileinfo['id'], True) # this file was downloaded successfully
        except Exception as e:
            logging.error(f"{threading.current_thread().name} failed to download {filename}")
            logging.error(e)
        return (fileinfo['id'], False)        

    # Function to update download status in the database
    def update_status(self, info):
        '''
        info: list of tuples (id, status)
        '''
        conn = pymysql.connect(
            **connection
        )
        cursor = conn.cursor()
        for i,s in info:
            if s:
                update_query = "UPDATE files SET downloaded = %s WHERE id = %s;"
                cursor.execute(update_query, (1, i))
        
        # Commit the changes and close the connection
        conn.commit()
        conn.close()

    def files_download(self):
        conn = pymysql.connect(
            **connection
        )
        cursor = conn.cursor()
        logging.info(f"counting files downloaded")        
        q = "select count(*) as count from files where downloaded = 1"
        cursor.execute(q)
        c = cursor.fetchone()
        logging.info(c)

        conn.close()
        return int(c['count'])

    def ntfy(self, msg):
        # send a post request to a url
        try:
            url = os.getenv('NTFY_URL')
            requests.post(url, data=msg)
        except:
            pass

def main():
    account = O365Account()
    initial_count = account.files_download() - 19014 # subtract tmp files
    c = 0
    batch_size = 1000
    t0 = time.time()
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        while True:
            files = account.fetch_files(batch_size=batch_size)
            if not files:
                logging.info("No more files to download. Exiting.")
                break
            
            logging.info('running thread pool')
            results = list(executor.map(account.download_file, files))
            logging.info(f"updating status")
            account.update_status(results)
            c += batch_size
            t1 = time.time()
            a = int(c / (t1 - t0))
            msg = f'downloaded {c+initial_count} files @ {a} files/sec'
            logging.info(msg)
            account.ntfy(msg)


if __name__ == '__main__':
    main()

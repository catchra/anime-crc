import os
import zlib
import re
import logging
import threading
from queue import Queue
import sys

import xattr

CRC_KEY = b"user.crc32"

def xattr_get_crc32(path):
    try:
        return xattr.get(path, CRC_KEY).decode()
    except (OSError, IOError, KeyError):
        return None

def xattr_set_crc32(path, value):
    try:
        xattr.set(path, CRC_KEY, str(value).encode())
    except (OSError, IOError):
        logging.warning(f"Could not set xattr for {path}")

def xattr_del_crc32(path):
    try:
        xattr.remove(path, CRC_KEY)
    except (OSError, IOError, KeyError):
        pass

def stdout_is_tty():
    return sys.stdout.isatty()

def stderr_is_tty():
    return sys.stderr.isatty()

python_xattr_available = True

clear_progress = "\r" + " " * 80 + "\r"

class ChainCRCStorage:
    def __init__(self, stores):
        self.stores = stores

    def read(self, path):
        for store in self.stores:
            value = store.read(path)
            if value is not None:
                return value
        return None

    def write(self, path, value):
        for store in self.stores:
            store.write(path, value)

    def delete(self, path):
        for store in self.stores:
            store.delete(path)

def parse_store_list(value):
    parts = value.split(',')
    stores = []
    for part in parts:
        part = part.strip()
        if part == "xattr" and python_xattr_available:
            stores.append(XAttrCRCStorage())
        elif part == "filename":
            stores.append(FilenameCRCStorage())
    return ChainCRCStorage(stores)

class XAttrCRCStorage:
    def read(self, path):
        return xattr_get_crc32(path)

    def write(self, path, value):
        xattr_set_crc32(path, value)

    def delete(self, path):
        xattr_del_crc32(path)

class FilenameCRCStorage:
    def read(self, path):
        match = re.search(r"\[([A-Fa-f0-9]{8})\]", os.path.basename(path))
        return match.group(1).upper() if match else None

    def write(self, path, value):
        base = os.path.basename(path)
        if re.search(r"\[[A-Fa-f0-9]{8}\]", base):
            return
        root, ext = os.path.splitext(path)
        new_path = f"{root} [{value.upper()}]{ext}"
        os.rename(path, new_path)

    def delete(self, path):
        base = os.path.basename(path)
        new_base = re.sub(r"\s*\[[A-Fa-f0-9]{8}\]", "", base)
        if base != new_base:
            os.rename(path, os.path.join(os.path.dirname(path), new_base))

def recurse_file_list(paths, recursive):
    file_list = []
    for path in paths:
        if os.path.isfile(path):
            file_list.append(path)
        elif os.path.isdir(path) and recursive:
            for root, _, files in os.walk(path):
                for f in files:
                    file_list.append(os.path.join(root, f))
    return file_list

def calculate_crc32(path):
    with open(path, "rb") as f:
        crc = 0
        while chunk := f.read(131072):
            crc = zlib.crc32(chunk, crc)
        return f"{crc & 0xFFFFFFFF:08X}"

def worker_add_crc32(queue, reader, writer):
    while True:
        path = queue.get()
        if path is None:
            break
        try:
            crc = calculate_crc32(path)
            writer.write(path, crc)
        except Exception as e:
            logging.error(f"Error processing {path}: {e}")
        queue.task_done()

def add_crc32_tags(paths, reader, writer, show_progress):
    queue = Queue()
    num_threads = 4
    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=worker_add_crc32, args=(queue, reader, writer))
        t.start()
        threads.append(t)

    for path in paths:
        queue.put(path)

    queue.join()

    for _ in range(num_threads):
        queue.put(None)
    for t in threads:
        t.join()

def worker_check_files(queue, warn_no_crc, reader):
    while True:
        path = queue.get()
        if path is None:
            break
        try:
            expected_crc = reader.read(path)
            actual_crc = calculate_crc32(path)
            if expected_crc and expected_crc.upper() != actual_crc:
                logging.error(f"CRC mismatch for {path}: expected {expected_crc}, got {actual_crc}")
            elif not expected_crc and warn_no_crc:
                logging.warning(f"No CRC tag found for {path}")
        except Exception as e:
            logging.error(f"Error checking {path}: {e}")
        queue.task_done()

def check_files(paths, warn_no_crc, reader, show_progress):
    queue = Queue()
    num_threads = 4
    threads = []
    for _ in range(num_threads):
        t = threading.Thread(target=worker_check_files, args=(queue, warn_no_crc, reader))
        t.start()
        threads.append(t)

    for path in paths:
        queue.put(path)

    queue.join()

    for _ in range(num_threads):
        queue.put(None)
    for t in threads:
        t.join()

    return False

def delete_crc32_tags(paths, writer):
    for path in paths:
        try:
            writer.delete(path)
        except Exception as e:
            logging.error(f"Error deleting CRC tag from {path}: {e}")

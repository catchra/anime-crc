import os
import sys
import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from anime_crc.crc import (
    parse_store_list,
    add_crc32_tags,
    delete_crc32_tags,
    check_files,
    recurse_file_list,
    clear_progress
)


def execute_from_command_line():
    parser = argparse.ArgumentParser(
        description='CRC32 generator and checker.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    # Actions
    parser.add_argument('-a', '--addcrc32', nargs='+', metavar='<file>',
                        help='Generate CRC32 for files and rename them.')
    parser.add_argument('-d', '--delete', nargs='+', metavar='<file>',
                        help='Delete CRC32 tags in the specified files.')
    parser.add_argument('-c', '--check', nargs='+', metavar='<file>',
                        help='Check CRC32 of files.')
    # Recursive
    parser.add_argument('-r', '--recursive', action='store_true',
                        help='Recurse into directories.')
    # Log level
    parser.add_argument('--debug', action='store_const', dest='level',
                        const=logging.DEBUG, default=logging.INFO,
                        help='Enable debug logging.')
    parser.add_argument('--warning', action='store_const', dest='level',
                        const=logging.WARNING, default=logging.INFO,
                        help='Enable warning logging only.')
    parser.add_argument('-n', '--no-progress', action='store_true',
                        help='Disable progress reporting.')
    # Stores
    parser.add_argument('--read-from', type=parse_store_list,
                        default='filename,xattr',
                        help='Comma-separated list of tag stores for reading.')
    parser.add_argument('--write-to', type=parse_store_list,
                        default='filename',
                        help='Comma-separated list of tag stores for writing.')
    # Threads
    parser.add_argument('--threads', type=int, default=os.cpu_count(),
                        help='Number of threads to use (default: number of CPU cores).')

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(level=args.level, format='%(levelname)-8s %(message)s')

    # Determine progress reporting using sys.isatty directly
    progress = sys.stdout.isatty() and sys.stderr.isatty() and not args.no_progress

    # Helper to gather files
    def gather(paths):
        return recurse_file_list(paths, args.recursive)

    exit_error = False
    try:
        # ADD CRC
        if args.addcrc32:
            files = gather(args.addcrc32)
            with ThreadPoolExecutor(max_workers=args.threads) as executor:
                futures = {executor.submit(add_crc32_tags, [f], args.read_from, args.write_to, progress): f for f in files}
                try:
                    for future in as_completed(futures):
                        f = futures[future]
                        future.result()
                        logging.info(f"Added CRC32 for {f}")
                except KeyboardInterrupt:
                    logging.warning("Interrupted during add operation, cancelling threads...")
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise

        # DELETE CRC
        if args.delete:
            files = gather(args.delete)
            with ThreadPoolExecutor(max_workers=args.threads) as executor:
                futures = {executor.submit(delete_crc32_tags, [f], args.write_to): f for f in files}
                try:
                    for future in as_completed(futures):
                        f = futures[future]
                        future.result()
                        logging.info(f"Deleted CRC32 tag for {f}")
                except KeyboardInterrupt:
                    logging.warning("Interrupted during delete operation, cancelling threads...")
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise

        # CHECK CRC
        if args.check:
            files = gather(args.check)
            with ThreadPoolExecutor(max_workers=args.threads) as executor:
                futures = {executor.submit(check_files, [f], True, args.read_from, progress): f for f in files}
                try:
                    for future in as_completed(futures):
                        f = futures[future]
                        result = future.result()
                        if result:
                            exit_error = True
                        else:
                            logging.info(f"CRC32 check passed for {f}")
                except KeyboardInterrupt:
                    logging.warning("Interrupted during check operation, cancelling threads...")
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise

    except KeyboardInterrupt:
        exit_error = True

    if progress:
        sys.stderr.write(clear_progress)

    if exit_error:
        sys.exit(1)

if __name__ == '__main__':
    execute_from_command_line()

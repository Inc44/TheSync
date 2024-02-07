import argparse
import csv
import datetime
import hashlib
import multiprocessing
import os
import shutil


parser = argparse.ArgumentParser(description="Parse sync operation arguments.")
parser.add_argument(
    "source_directory",
    type=str,
    help="Specifies the source directory from which to sync files",
)
parser.add_argument(
    "destination_directory",
    type=str,
    help="Specifies the target directory to which files will be synced",
)
parser.add_argument(
    "-dm",
    "--decision_maker",
    type=str,
    choices=["user", "auto"],
    default="auto",
    help="Sets the conflict resolution mode (`user` for manual resolution or `auto` for automatic), with `auto` being the default",
)
parser.add_argument(
    "-dmfs",
    "--date_modified_fix_source",
    type=str,
    choices=["source_directory", "destination_directory"],
    default="destination_directory",
    help="Chooses the directory (`source_directory` or `destination_directory`) whose file modification dates are to be used for synchronization, with `destination_directory` as the default",
)
parser.add_argument(
    "-dmfe",
    "--date_modified_fix_enabled",
    action="store_true",
    help="Toggles the date modification fix feature on or off, with it being enabled by default",
)
parser.add_argument(
    "-vs",
    "--verify_steps",
    type=int,
    default=0,
    help="Sets the number of verification steps, with the default set to 0",
)
parser.add_argument(
    "-vh",
    "--verify_hash",
    action="store_true",
    help="Turns on hash verification for a more thorough integrity check, though it is off by default",
)
parser.add_argument(
    "-ppm",
    "--parallel_processing_mode",
    action="store_false",
    help="Enables or disables parallel processing, with it being disabled by default or if manual resolution mode is used",
)
parser.add_argument(
    "-tc",
    "--threads_count",
    type=int,
    default=8,
    help="Determines the number of threads to use for parallel processing, with 8 threads as the default",
)
arguments = parser.parse_args()


sync_tsv_file = "sync_data.tsv"
tsv_headers = [
    "source_path",
    "source_path_size",
    "source_path_date",
    "destination_path",
    "destination_path_size",
    "destination_path_date",
]


def initialize_tsv():
    with open(sync_tsv_file, mode="w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=tsv_headers, delimiter="\t")
        writer.writeheader()


def insert_file_data_tsv(
    source_path,
    source_path_size,
    source_path_date,
    destination_path,
    destination_path_size,
    destination_path_date,
):
    with open(sync_tsv_file, mode="a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=tsv_headers, delimiter="\t")
        writer.writerow(
            {
                "source_path": source_path,
                "source_path_size": source_path_size,
                "source_path_date": source_path_date,
                "destination_path": destination_path,
                "destination_path_size": destination_path_size,
                "destination_path_date": destination_path_date,
            }
        )


def walk_directory_and_fill_tsv(source_directory, destination_directory):
    for source_root, _, files in os.walk(source_directory):
        for file in files:
            source_path = os.path.join(source_root, file)
            relative_path = os.path.relpath(source_path, source_directory)
            destination_path = os.path.join(destination_directory, relative_path)
            source_path_size = os.path.getsize(source_path)
            source_path_date = os.path.getmtime(source_path)
            destination_path_size = (
                os.path.getsize(destination_path)
                if os.path.exists(destination_path)
                else 0
            )
            destination_path_date = (
                os.path.getmtime(destination_path)
                if os.path.exists(destination_path)
                else 0
            )
            insert_file_data_tsv(
                source_path,
                source_path_size,
                source_path_date,
                destination_path,
                destination_path_size,
                destination_path_date,
            )


def format_size(bytes, suffix="B"):
    for unit in ["", "K", "M", "G", "T", "P", "E", "Z"]:
        if abs(bytes) < 1024.0:
            return f"{bytes:3.1f}{unit}{suffix}"
        bytes /= 1024.0
    return f"{bytes:.1f}Yi{suffix}"


def format_date(timestamp):
    return datetime.datetime.fromtimestamp(timestamp).strftime("%Y/%m/%d %H:%M:%S")


def user_decision_prompt(
    source_path,
    source_path_size,
    source_path_date,
    destination_path,
    destination_path_size,
    destination_path_date,
):
    source_size_formatted = format_size(source_path_size)
    destination_size_formatted = format_size(destination_path_size)
    source_date_formatted = format_date(source_path_date)
    destination_date_formatted = format_date(destination_path_date)

    message = (
        f"File '{source_path}' differs from '{destination_path}'.\n"
        f"Source size: {source_size_formatted}, Last modified: {source_date_formatted}\n"
        f"Destination size: {destination_size_formatted}, Last modified: {destination_date_formatted}\n"
        "Do you want to override the destination file with the source file? (y/n): "
    )

    return input(message)


def process_sync_entry(row):
    source_path = row["source_path"]
    source_path_size = int(row["source_path_size"])
    source_path_date = float(row["source_path_date"])
    destination_path = row["destination_path"]
    destination_path_size = int(row["destination_path_size"])
    destination_path_date = float(row["destination_path_date"])
    if source_path_size != destination_path_size:
        if arguments.decision_maker == "auto":
            shutil.copy2(source_path, destination_path)
            print(f"Auto-synced: {destination_path}")
        elif arguments.decision_maker == "user":
            user_choice = user_decision_prompt(
                source_path,
                source_path_size,
                source_path_date,
                destination_path,
                destination_path_size,
                destination_path_date,
            )
            if user_choice.lower() == "y":
                shutil.copy2(source_path, destination_path)
                print(f"User-synced: {destination_path}")
    if source_path_date != destination_path_date:
        if arguments.date_modified_fix_enabled:
            if arguments.date_modified_fix_source == "destination_directory":
                os.utime(source_path, (destination_path_date, destination_path_date))
            elif arguments.date_modified_fix_source == "source_directory":
                os.utime(destination_path, (source_path_date, source_path_date))


def process_sync_entries(parallel_processing_mode, threads_count):
    if parallel_processing_mode and arguments.decision_maker == "auto":
        with open(sync_tsv_file, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file, delimiter="\t")
            entries = [row for row in reader]
        with multiprocessing.Pool(
            processes=threads_count or multiprocessing.cpu_count()
        ) as pool:
            results = pool.map(process_sync_entry, entries)
            for result in results:
                if result:
                    print(result)
    else:
        with open(sync_tsv_file, mode="r", encoding="utf-8") as file:
            reader = csv.DictReader(file, delimiter="\t")
            for row in reader:
                result = process_sync_entry(row)
                if result:
                    print(result)


def sync():
    initialize_tsv()
    walk_directory_and_fill_tsv(
        arguments.source_directory, arguments.destination_directory
    )
    process_sync_entries(arguments.parallel_processing_mode, arguments.threads_count)


def generate_file_hash(file_path):
    hash_algo = hashlib.sha512()
    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(4096), b""):
            hash_algo.update(chunk)
    return hash_algo.hexdigest()


def compare_file_hashes():
    with open(sync_tsv_file, mode="r", encoding="utf-8") as file:
        reader = csv.DictReader(file, delimiter="\t")
        for row in reader:
            source_hash = (
                generate_file_hash(row["source_path"])
                if os.path.exists(row["source_path"])
                else None
            )
            destination_hash = (
                generate_file_hash(row["destination_path"])
                if os.path.exists(row["destination_path"])
                else None
            )
            if source_hash != destination_hash:
                print(
                    f"Hash mismatch: {row['source_path']} and {row['destination_path']}"
                )


def get_relative_file_paths(directory):
    file_paths = []
    for root, _, files in os.walk(directory):
        for file in files:
            relative_path = os.path.relpath(os.path.join(root, file), directory)
            file_paths.append(relative_path)
    return file_paths


def create_sync_report(
    missing_in_source, missing_in_destination, source_directory, destination_directory
):
    missing_in_source.sort()
    missing_in_destination.sort()

    if missing_in_destination:
        print("Files missing in destination:")
        for file in missing_in_destination:
            full_path = os.path.join(source_directory, file)
            print(full_path)

    if missing_in_source:
        print("Files missing in source:")
        for file in missing_in_source:
            full_path = os.path.join(destination_directory, file)
            print(full_path)


if __name__ == "__main__":
    sync()
    if arguments.verify_steps > 0:
        for step in range(arguments.verify_steps):
            sync()
    if arguments.verify_hash:
        compare_file_hashes()
        print(arguments.verify_hash)
    source_files = set(get_relative_file_paths(arguments.source_directory))
    destination_files = set(get_relative_file_paths(arguments.destination_directory))
    missing_in_destination = list(source_files - destination_files)
    missing_in_source = list(destination_files - source_files)
    create_sync_report(
        missing_in_source,
        missing_in_destination,
        arguments.source_directory,
        arguments.destination_directory,
    )

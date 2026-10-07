from __future__ import annotations

import json
import re
import logging
import time

from pathlib import Path
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, Query

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# -----------------------------------------------------------------------------
# JSON Folder
# -----------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

JSON_FOLDER = BASE_DIR / "json_outputs"

# -----------------------------------------------------------------------------
# Redis Connection
# -----------------------------------------------------------------------------

r = redis.Redis(
    host="localhost",
    port=6379,
    decode_responses=True
)

# -----------------------------------------------------------------------------
# Regex
# -----------------------------------------------------------------------------

_non_alnum_pattern = re.compile(r"[^a-z0-9]+")
_multi_space_pattern = re.compile(r"\s+")

# -----------------------------------------------------------------------------
# Normalize
# -----------------------------------------------------------------------------

def normalize(text: str):

    text = str(text)

    text = text.lower()

    text = _non_alnum_pattern.sub(" ", text)

    text = _multi_space_pattern.sub(" ", text).strip()

    return text


# -----------------------------------------------------------------------------
# Tokenize
# -----------------------------------------------------------------------------

def tokenize(text: str):

    return [
        token
        for token in normalize(text).split()
        if token
    ]
# -----------------------------------------------------------------------------
# Load All JSON Files into Redis
# -----------------------------------------------------------------------------

def load_snapshot():

    logging.info("Loading JSON files into Redis...")

    # Delete old keys
    for key in r.scan_iter("report:*"):
        r.delete(key)

    json_files = list(JSON_FOLDER.glob("*.json"))

    logging.info(f"Found {len(json_files)} JSON files")

    total_records = 0

    for json_file in json_files:

        logging.info(f"Processing {json_file.name}")

        with open(json_file, "r", encoding="utf-8") as f:

            rows = json.load(f)

        for row in rows:

            report_name = normalize(row["REPORT_NAME"]).replace(" ", "_")

            # Find the actual variable name
            variable_name = None

            for key in row.keys():

                if key != "REPORT_NAME":

                    variable_name = key

                    break

            if variable_name is None:
                continue
            variable_value = str(row[variable_name])
            normalized_value = normalize(variable_value)

            tokens = {normalized_value}

            for token in tokens:

                for i in range(1, len(token) + 1):

                    prefix = token[:i]

                    redis_key = (
                        f"report:{report_name}:"
                        f"{variable_name.lower()}:{prefix}"
                    )

                    r.sadd(
                        redis_key,
                        variable_value
                    )

            total_records += 1

    logging.info(
        f"Loaded {total_records} records into Redis."
    )    
    # -----------------------------------------------------------------------------
# FastAPI Lifespan
# -----------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):

    load_snapshot()

    yield


# -----------------------------------------------------------------------------
# FastAPI App
# -----------------------------------------------------------------------------

app = FastAPI(
    lifespan=lifespan
)
# -----------------------------------------------------------------------------
# Run Server
# -----------------------------------------------------------------------------
if __name__ == "__main__":

    print("Script started")

    load_snapshot()

    print("Script finished")
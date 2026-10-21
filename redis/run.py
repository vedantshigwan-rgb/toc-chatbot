import os
from pathlib import Path

import oracledb
from dotenv import load_dotenv

# Load environment variables from .env in the same folder as this script

dotenv_path = Path(r"D:\TOC-chatbot\TOC-chatbot\redis\.env")
load_dotenv(dotenv_path)

# Oracle wallet connection settings
ORACLE_USER = os.getenv("ORACLE_USER")
ORACLE_PASS = os.getenv("ORACLE_PASS")
ORACLE_WALLET_PASSWORD = os.getenv("ORACLE_WALLET_PASSWORD")
WALLET_PATH = os.getenv("WALLET_PATH")
TNS_ALIAS = os.getenv("TNS_ALIAS")
ORACLE_DSN = os.getenv("ORACLE_DSN")

print("[DEBUG] .env loaded from:", dotenv_path)
print("[DEBUG] ORACLE_USER set:", bool(ORACLE_USER))
print("[DEBUG] ORACLE_PASS set:", bool(ORACLE_PASS))
print("[DEBUG] WALLET_PATH:", WALLET_PATH)
print("[DEBUG] TNS_ALIAS:", TNS_ALIAS)
print("[DEBUG] ORACLE_DSN:", bool(ORACLE_DSN))
print("[DEBUG] Oracle thin mode:", oracledb.is_thin_mode())

if not ORACLE_USER or not ORACLE_PASS:
    raise RuntimeError("Missing Oracle credentials: ORACLE_USER and ORACLE_PASS must be set")

if not WALLET_PATH:
    raise RuntimeError("Missing WALLET_PATH environment variable")

wallet_location = WALLET_PATH
connection_dsn = ORACLE_DSN or TNS_ALIAS

if not connection_dsn:
    raise RuntimeError("Missing ORACLE_DSN or TNS_ALIAS environment variable")

connection = oracledb.connect(
    user=ORACLE_USER,
    password=ORACLE_PASS,
    dsn=connection_dsn,
    config_dir=WALLET_PATH,
    wallet_location=wallet_location,
    wallet_password=ORACLE_WALLET_PASSWORD,
)

print("[DEBUG] config_dir:", WALLET_PATH)
print("Successfully connected to Oracle Database")

with connection.cursor() as cursor:
    for row in cursor.execute("""
        select distinct buyer_name
        FROM KPCL_REPORTING.KPC_FULL_KIT_INV_SUMMARY
        fetch first 5 rows only
    """):
        if row[0]:
            print(row[0], "is done")
        else:
            print("buyer_name is NULL or empty")

connection.close()
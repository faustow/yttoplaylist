#!/usr/bin/env python3
import sys
print("Step 1: basic imports", flush=True)
import argparse, asyncio, json, logging, os, re, ssl, subprocess, tempfile, warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

print("Step 2: certifi", flush=True)
import certifi

print("Step 3: aiohttp", flush=True)
import aiohttp

print("Step 4: aiohttp_retry", flush=True)
from aiohttp_retry import RetryClient, ExponentialRetry

print("Step 5: shazamio", flush=True)
from shazamio import Shazam

print("Step 6: shazamio.client", flush=True)
from shazamio.client import HTTPClient

print("Step 7: shazamio.exceptions", flush=True)
from shazamio.exceptions import BadMethod

print("Step 8: shazamio.utils", flush=True)
from shazamio.utils import validate_json

print("All imports OK!", flush=True)

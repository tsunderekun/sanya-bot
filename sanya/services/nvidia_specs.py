import asyncio
import re
from html import unescape

import httpx

RTX_3070_PAGE = "https://www.nvidia.com/en-us/geforce/graphics-cards/30-series/rtx-3070-3070ti/"
_MODEL = re.compile(r"\bRTX\s*3070(?:\s*Ti\b)?\b", re.I)
_TABLE = re.compile(r"<table\b[^>]*>(.*?)</table>", re.S | re.I)
_ROW = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)


def _model(text: str) -> str:
    return " ".join(text.upper().replace("RTX3070", "RTX 3070").split())


def requested_model(query: str) -> str | None:
    if not re.search(r"(?:\bnvidia\b|\bнвидиа\b)", query, re.I) or not re.search(
        r"характеристик|спецификац|\bspecs\b|\bspecifications\b|cuda|видеопамят|\bvram\b", query, re.I
    ):
        return None
    models = {_model(m[0]) for m in _MODEL.finditer(query)}
    other_models = re.findall(r"\b(?:rtx|gtx|rx)\s*\d{3,4}\b", query, re.I)
    if any(_model(m) != "RTX 3070" for m in other_models) or re.search(
        r"\b(?:laptop|mobile|compare|msi|asus|gigabyte|palit|zotac)\b|ноутбук|мобильн|сравн", query, re.I
    ):
        return None
    return next(iter(models)) if len(models) == 1 else None


def parse_specs(page: str, model: str) -> str:
    for table in _TABLE.findall(page):
        index = None
        width = 0
        cores = memory = size = kind = boost = ""
        for row in _ROW.findall(table):
            cells = [" ".join(unescape(re.sub(r"<[^>]*>", "", c)).split()) for c in _CELL.findall(row)]
            headers = [_MODEL.fullmatch(re.sub(r"^G\s*eForce\s+", "", c, flags=re.I)) for c in cells]
            if any(headers):
                matches = [i for i, m in enumerate(headers) if m and _model(m[0]) == model]
                index = matches[0] if len(matches) == 1 else None
                width = len(cells)
                cores = memory = size = kind = boost = ""
                continue
            if index is None or not 1 <= index < len(cells) or len(cells) != width:
                continue
            label = cells[1 if width >= 4 else 0]
            value = cells[index]
            if re.fullmatch(r"NVIDIA CUDA\s*®?\s*Cores", label, re.I) and re.fullmatch(r"[\d, ]{3,7}", value):
                cores = value
            elif label == "Standard Memory Config" and re.fullmatch(r"\d+ GB GDDR\dX?", value):
                memory = value
            elif label == "Memory Size" and re.fullmatch(r"\d+ GB", value):
                size = value
            elif label == "Memory Type" and re.fullmatch(r"GDDR\dX?", value):
                kind = value
            elif label in ("Boost Clock", "Boost Clock (GHz)") and re.fullmatch(r"\d\.\d{1,2}(?: GHz)?", value):
                boost = value.removesuffix(" GHz")
        memory = memory or (f"{size} {kind}" if size and kind else "")
        if cores and memory:
            lines = [f"**GeForce {model} — по данным NVIDIA**", f"• CUDA-ядра: {cores}", f"• Видеопамять: {memory.replace(' GB ', ' ГБ ')}"]
            if boost:
                lines.append(f"• Частота Boost: {boost} ГГц")
            return "\n".join(lines)
    return ""


async def fetch_specs(model: str) -> str:
    try:
        async def read() -> str:
            async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", RTX_3070_PAGE) as response:
                    response.raise_for_status()
                    if "text/html" not in response.headers.get("content-type", ""):
                        return ""
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(data) + len(chunk) > 2_000_000:
                            return ""
                        data.extend(chunk)
                    return parse_specs(data.decode("utf-8", errors="replace"), model)

        return await asyncio.wait_for(read(), timeout=4)
    except (httpx.HTTPError, asyncio.TimeoutError):
        return ""

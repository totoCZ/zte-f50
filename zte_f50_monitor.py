#!/usr/bin/env python3
"""
ZTE F50 Modem Tower Stats Monitor — iftop-style TUI
Supports LTE, NSA (LTE+NR), and SA (NR) modes.
"""

import requests
import time
import sys
import hashlib
import os
import shutil
import string
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from dotenv import load_dotenv

# Load .env from the same directory as this script, regardless of CWD
load_dotenv(Path(__file__).parent / '.env')


# ── crypto helpers ────────────────────────────────────────────────────────────

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode('utf-8')).hexdigest().upper()

def _md5(s: str) -> str:
    return hashlib.md5(s.encode('utf-8')).hexdigest()

def _compute_password(plaintext: str, ld: str) -> str:
    return _sha256(_sha256(plaintext) + ld)

def _compute_ad(cr_version: str, wa_inner_version: str, rd: str) -> str:
    # AD = SHA256(SHA256(wa_inner_version + cr_version) + RD), all uppercase
    return _sha256(_sha256(wa_inner_version + cr_version) + rd)


# ── ANSI helpers ──────────────────────────────────────────────────────────────

RESET  = '\033[0m'
BOLD   = '\033[1m'
DIM    = '\033[2m'
RED    = '\033[31m'
YELLOW = '\033[33m'
GREEN  = '\033[32m'
CYAN   = '\033[36m'
WHITE  = '\033[37m'

def _clear():
    print('\033[2J\033[H', end='')

def _rsrp_color(rsrp: Optional[int]) -> str:
    if rsrp is None: return DIM
    if rsrp >= -80:  return GREEN
    if rsrp >= -90:  return YELLOW
    return RED

def _bars(rsrp: Optional[int]) -> str:
    if rsrp is None: return '░░░░░'
    if rsrp >= -70:  n = 5
    elif rsrp >= -80: n = 4
    elif rsrp >= -90: n = 3
    elif rsrp >= -100: n = 2
    else: n = 1
    c = _rsrp_color(rsrp)
    return c + '█' * n + DIM + '░' * (5 - n) + RESET

def _ago(ts: float) -> str:
    s = int(time.time() - ts)
    if s < 60:  return f'{s}s'
    return f'{s//60}m{s%60:02d}s'

def _cols() -> int:
    return shutil.get_terminal_size((80, 24)).columns

def _rsrq_color(rsrq: Optional[int]) -> str:
    if rsrq is None: return DIM
    if rsrq >= -6:   return GREEN
    if rsrq >= -12:  return YELLOW
    return RED

def _snr_color(snr: Optional[int]) -> str:
    if snr is None: return DIM
    if snr >= 13:   return GREEN
    if snr >= 0:    return YELLOW
    return RED

# LTE EARFCN → DL frequency (MHz). (band, offset_earfcn, dl_low_mhz)
_LTE_DL_TABLE = {
    1:  (0,     2110.0),
    2:  (600,   1930.0),
    3:  (1200,  1805.0),
    4:  (1950,  2110.0),
    5:  (2400,   869.0),
    7:  (2750,  2620.0),
    8:  (3450,   925.0),
    12: (5010,   729.0),
    13: (5180,   746.0),
    17: (5730,   734.0),
    20: (6150,   791.0),
    25: (8040,  1930.0),
    26: (8690,   859.0),
    28: (9210,   758.0),
    38: (37750, 2570.0),
    40: (38650, 2300.0),
    41: (39650, 2496.0),
    42: (41590, 3400.0),
    43: (43590, 3600.0),
}

def _earfcn_to_mhz(band: int, earfcn: int) -> Optional[float]:
    """LTE EARFCN → DL centre frequency in MHz."""
    entry = _LTE_DL_TABLE.get(band)
    if entry is None:
        return None
    n_offs, f_low = entry
    return round(f_low + 0.1 * (earfcn - n_offs), 2)

def _nrarfcn_to_mhz(arfcn: int) -> float:
    """NR-ARFCN → centre frequency in MHz (3GPP TS 38.104 Table 5.4.2.1-1)."""
    if arfcn < 600000:
        return round(arfcn * 0.005, 3)
    if arfcn < 2016667:
        return round(3000.0 + (arfcn - 600000) * 0.015, 3)
    return round(24250.08 + (arfcn - 2016667) * 0.060, 3)

def _is_nr_arfcn(earfcn: int) -> bool:
    """LTE EARFCNs top out at 67835; anything higher is an NR-ARFCN."""
    return earfcn > 67835

def _decode_lte_cell_id(cell_id: int) -> Tuple[int, int]:
    """Split 28-bit E-UTRAN Cell ID into (eNB_ID, sector_index)."""
    return cell_id >> 8, cell_id & 0xFF


# ── signal assessment ───────────────────────────────────────────────────────────

def _get_signal_assessment(rsrp: Optional[int], snr: Optional[int]) -> Tuple[str, str, str]:
    """
    Assesses signal quality based on RSRP (strength) and SNR (quality).
    Returns a tuple of (rating, description, color).
    """
    # Handle missing data
    if rsrp is None or snr is None:
        return ("No Data", "Waiting for signal data... (｡•́_•̀｡)", DIM)

    # Determine rating and description based on RSRP and SNR combinations
    if rsrp >= -80:  # Strong signal
        if snr > 20:
            return ("Excellent", "Super strong signal! (｡◕‿◕｡)", GREEN)
        elif snr > 10:
            return ("Good", "Strong signal, very clear! (◕‿◕)", GREEN)
        else:
            return ("Fair", "Great signal strength, but some noise. (｡•́︿•̀｡)", YELLOW)
    elif rsrp >= -90:  # Good signal
        if snr > 20:
            return ("Good", "Good signal, very clean! (◕‿◕)", GREEN)
        elif snr > 10:
            return ("Fair", "Decent connection. (๑•̀ㅂ•́)و✧", YELLOW)
        else:
            return ("Poor", "Signal is okay, but it's pretty noisy. (´•̥ ω •̥` )", RED)
    elif rsrp >= -100:  # Weak signal
        if snr > 10:
            return ("Fair", "Weak signal, but it's clean! Trying my best... (｡•́_•̀｡)", YELLOW)
        else:
            return ("Poor", "Signal is weak and noisy. (╥﹏╥)", RED)
    else:  # Very poor signal
        return ("Very Poor", "Signal is very weak and noisy. (╥﹏╥)", RED)


# ── modem client ──────────────────────────────────────────────────────────────

NETWORK_TYPES = {
    0: 'No Service', 1: 'GSM', 2: 'GPRS', 3: 'EDGE',
    4: 'WCDMA', 5: 'HSDPA', 6: 'HSUPA', 7: 'HSPA+',
    8: 'LTE', 9: 'LTE-A', 13: 'LTE', 41: 'NR5G-NSA', 43: 'NR5G-SA',
}

class ZTEF50Monitor:
    def __init__(self, host: Optional[str] = None, username: Optional[str] = None,
                 password: Optional[str] = None):
        self.host = host or os.getenv("ZTE_HOST", "10.50.0.1")
        self.base_url = f"http://{self.host}"
        self.username = username or os.getenv("ZTE_USER", "admin")
        self.password = password or os.getenv("ZTE_PASS", "")
        self._new_session()
        self.logged_in = False
        self.cr_version = self.wa_inner_version = self.ld = self.rd = ""

    def _new_session(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': f'http://{self.host}/index.html',
        })
        
    def _get(self, cmd: str, extra: Optional[dict] = None) -> Optional[dict]:
        params = {'isTest': 'false', 'cmd': cmd}
        if extra:
            params.update(extra)
        r = self.session.get(
            f'{self.base_url}/goform/goform_get_cmd_process',
            params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    def _fetch_nonces(self) -> bool:
        ver = self._get('Language,cr_version,wa_inner_version', {'multi_data': '1'})
        self.cr_version = ver.get('cr_version', '')
        self.wa_inner_version = ver.get('wa_inner_version', '')
        self.ld = self._get('LD').get('LD', '')
        self.rd = self._get('RD').get('RD', '')
        return bool(self.ld and self.rd)

    def login(self) -> bool:
        try:
            if not self._fetch_nonces():
                return False
            r = self.session.post(
                f'{self.base_url}/goform/goform_set_cmd_process',
                data={
                    'isTest': 'false',
                    'goformId': 'LOGIN_MULTI_USER',
                    'user': self.username,
                    'password': _compute_password(self.password, self.ld),
                    'AD': _compute_ad(self.cr_version, self.wa_inner_version, self.rd),
                    }, timeout=10)
            resp = r.json()
            if resp.get('loginfo') == 'ok' or str(resp.get('result')) == '0':
                self.logged_in = True
                return True
            return False
        except Exception:
            self.logged_in = False
            return False

    def _logout(self):
        try:
            self.session.post(
                f'{self.base_url}/goform/goform_set_cmd_process',
                data={'isTest': 'false', 'goformId': 'LOGOFF',
                      'AD': _compute_ad(self.cr_version, self.wa_inner_version, self.rd)},
                timeout=10)
        except Exception:
            pass

    def fetch(self) -> Optional[Dict[str, Any]]:
        if not self.logged_in:
            if not self.login():
                return None
        try:
            ts = int(time.time() * 1000)
            merged: Dict[str, Any] = {}
            
  
            for cmd, extra in [
                ('network_information,Lte_ca_status',
                 {'multi_data': '1', '_': str(ts + 2)}),
                ('neighbor_cell_info', {'_': str(ts + 3)}),
            ]:
                d = self._get(cmd, extra)
                if d:
                    merged.update(d)
            return merged or None
        except requests.exceptions.ConnectionError:
            self.logged_in = False
        except Exception:
            pass
        return None


# ── TUI renderer ──────────────────────────────────────────────────────────────

def _divider(width: int, label: str = '') -> str:
    if label:
        pad = width - len(label) - 4
        return f'{DIM}── {RESET}{BOLD}{label}{RESET}{DIM} ' + '─' * max(0, pad) + RESET
    return DIM + '─' * width + RESET

def _kv(label: str, value: Any, width: int = 22) -> str:
    return f'  {DIM}{label:<{width}}{RESET}{value}'

_NR_SENTINEL = 9223372036854775807  # INT64_MAX — modem reports this when NR cell ID is unknown

def _val(v: Any) -> str:
    """Blank-safe value display."""
    s = str(v).strip()
    return s if s and s not in ('null', '') else '—'

def _nr_cell_id(v: Any) -> str:
    try:
        return '—' if int(v) == _NR_SENTINEL else str(v)
    except (TypeError, ValueError):
        return _val(v)


def render(stats: Dict[str, Any], seen: 'OrderedDict[str, dict]',
           last_ok: float, error: Optional[str]) -> str:
    W = _cols()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines: List[str] = []

    # ── header ────────────────────────────────────────────────────────────────
    title = f'{BOLD}{CYAN}ZTE F50{RESET}  {DIM}{now}{RESET}'
    hint  = f'{DIM}Ctrl+C to quit{RESET}'
    gap   = W - 16 - 14  # approx
    lines.append(f'{title}{"":>{gap}}{hint}')
    lines.append(_divider(W))

    if error:
        lines.append(f'  {RED}{BOLD}ERROR:{RESET} {error}  '
                     f'{DIM}(last data {_ago(last_ok)} ago){RESET}')
        lines.append('')
    else:
        # ── mode detection ──────────────────────────────────────────────────────
        nt_raw = stats.get('network_type')
        nt_str = NETWORK_TYPES.get(nt_raw, str(nt_raw)) if isinstance(nt_raw, int) else _val(nt_raw)
        
        # Detect NR presence
        nr_rsrp_raw = stats.get('nr_rsrp')
        has_nr = nr_rsrp_raw not in (None, '', 'null')
        
        # Detect LTE presence
        lte_rsrp_raw = stats.get('lte_rsrp')
        has_lte = lte_rsrp_raw not in (None, '', 'null')

        # Determine display mode
        if has_nr and has_lte:
            mode_str = "LTE + NR5G (NSA)"
        elif has_nr:
            mode_str = "NR5G (SA)"
            nt_str = "NR5G-SA" # Override network type string if SA
        elif has_lte:
            mode_str = "LTE"
        else:
            mode_str = "No Service"

        # ── primary status line ─────────────────────────────────────────────────
        # Prefer NR signal bars if available, else LTE
        primary_rsrp = None
        if has_nr:
            primary_rsrp = int(nr_rsrp_raw) if nr_rsrp_raw else None
        elif has_lte:
            primary_rsrp = int(lte_rsrp_raw) if lte_rsrp_raw else None
            
        bar_str = _bars(primary_rsrp)
        
        # Assessment for primary connection
        # Use NR SNR if available
        snr_val = None
        if has_nr:
            snr_raw = stats.get('Nr_snr')
            snr_val = int(snr_raw) if snr_raw not in (None, '', 'null') else None
        elif has_lte:
            snr_raw = stats.get('Lte_snr')
            snr_val = int(snr_raw) if snr_raw not in (None, '', 'null') else None

        rating, description, rating_color = _get_signal_assessment(primary_rsrp, snr_val)
        rating_c = f'{rating_color}{BOLD}{rating}{RESET}'
        description_c = f'{DIM}{description}{RESET}'

        lines.append(_divider(W, 'CONNECTION'))
        lines.append(f'  {BOLD}Network{RESET}  {nt_str}   {bar_str}  {rating_c}')
        lines.append(f'  {description_c}')
        lines.append('')

        # ── NR5G SECTION (SA or NSA Secondary) ───────────────────────────────────
        if has_nr:
            nr_rsrp = int(nr_rsrp_raw) if nr_rsrp_raw else None
            nr_rsrq = stats.get('nr_rsrq')
            nr_snr  = stats.get('Nr_snr')
            nr_snr_i = int(nr_snr) if nr_snr not in (None, '', 'null') else None
            
            nr_band = _val(stats.get('Nr_bands'))
            nr_fcn  = _val(stats.get('Nr_fcn'))
            nr_pci  = _val(stats.get('Nr_pci'))
            nr_bw   = _val(stats.get('Nr_band_widths'))
            
            nr_freq_str = ''
            try:
                nr_freq_str = f'  ·  {_nrarfcn_to_mhz(int(nr_fcn))} MHz'
            except (ValueError, TypeError):
                pass

            lines.append(_divider(W, 'NR5G'))
            lines.append(_kv('RSRP', f'{_rsrp_color(nr_rsrp)}{nr_rsrp if nr_rsrp is not None else "—"} dBm{RESET}'))
            lines.append(_kv('RSRQ', f'{_rsrq_color(int(nr_rsrq) if nr_rsrq not in (None,"","null") else None)}{_val(nr_rsrq)} dB{RESET}'))
            lines.append(_kv('SNR',  f'{_snr_color(nr_snr_i)}{nr_snr_i if nr_snr_i is not None else "—"} dB{RESET}'))
            nr_band_str = f'n{nr_band}' if nr_band != '—' else '—'
            lines.append(_kv('Band / ARFCN', f'{nr_band_str}  /  {nr_fcn}{nr_freq_str}'))
            lines.append(_kv('Cell ID / PCI', f'{_nr_cell_id(stats.get("Nr_cell_id"))}  /  {nr_pci}'))
            lines.append(_kv('Bandwidth', f'{nr_bw} MHz' if nr_bw != '—' else '—'))
            lines.append('')

        # ── LTE SECTION (SA or NSA Anchor) ───────────────────────────────────────
        if has_lte:
            lte_rsrp = int(lte_rsrp_raw) if lte_rsrp_raw else None
            lte_rsrq = stats.get('lte_rsrq')
            lte_snr  = stats.get('Lte_snr')
            lte_snr_i = int(lte_snr) if lte_snr not in (None, '', 'null') else None
            
            lte_band = _val(stats.get('Lte_bands'))
            lte_fcn  = _val(stats.get('Lte_fcn'))
            lte_bw_khz = stats.get('Lte_bands_widths')
            lte_bw_str = f'{int(lte_bw_khz)//1000} MHz' if lte_bw_khz else '—'
            lte_pci  = _val(stats.get('Lte_pci'))
            lte_cell_id = _val(stats.get('Lte_cell_id'))
            ca       = _val(stats.get('Lte_ca_status'))

            lte_freq_str = ''
            try:
                f = _earfcn_to_mhz(int(lte_band), int(lte_fcn))
                if f is not None:
                    lte_freq_str = f'  ·  {f} MHz'
            except (ValueError, TypeError):
                pass

            lte_cell_id_extra = ''
            try:
                cid = int(lte_cell_id)
                enb, sector = _decode_lte_cell_id(cid)
                lte_cell_id_extra = f'  {DIM}(eNB {enb}, s{sector}){RESET}'
            except (ValueError, TypeError):
                pass

            lte_rsrq_i = int(lte_rsrq) if lte_rsrq not in (None, '', 'null') else None

            lines.append(_divider(W, 'LTE'))
            lines.append(_kv('RSRP', f'{_rsrp_color(lte_rsrp)}{lte_rsrp if lte_rsrp is not None else "—"} dBm{RESET}'))
            lines.append(_kv('RSRQ', f'{_rsrq_color(lte_rsrq_i)}{_val(lte_rsrq)} dB{RESET}'))
            lines.append(_kv('SNR',  f'{_snr_color(lte_snr_i)}{lte_snr_i if lte_snr_i is not None else "—"} dB{RESET}'))
            lines.append(_kv('RSSI', f'{_val(stats.get("lte_rssi"))} dBm'))
            lte_band_str = f'B{lte_band}' if lte_band != '—' else '—'
            lines.append(_kv('Band / EARFCN', f'{lte_band_str}  /  {lte_fcn}  ({lte_bw_str}){lte_freq_str}'))
            lines.append(_kv('Cell ID / PCI', f'{lte_cell_id}  /  {lte_pci}{lte_cell_id_extra}'))
            lines.append(_kv('CA', ca))
            lines.append('')

    # ── neighbor table ────────────────────────────────────────────────────────
    lines.append(_divider(W, f'NEIGHBORS  (last 60s — {len(seen)} cells)'))

    # Collect serving cell identifiers for neighbor annotation
    serving_lte_pci = str(stats.get('Lte_pci', '')).strip()
    serving_lte_fcn = str(stats.get('Lte_fcn', '')).strip()
    serving_nr_pci  = str(stats.get('Nr_pci', '')).strip()
    serving_nr_fcn  = str(stats.get('Nr_fcn', '')).strip()

    def _nb_is_serving(cell: dict) -> bool:
        earfcn_s = str(cell.get('earfcn', '')).strip()
        pci_s    = str(cell.get('pci', '')).strip()
        try:
            nr = _is_nr_arfcn(int(earfcn_s))
        except (ValueError, TypeError):
            nr = False
        if nr:
            return pci_s == serving_nr_pci and earfcn_s == serving_nr_fcn
        return pci_s == serving_lte_pci and earfcn_s == serving_lte_fcn

    def _nb_freq(cell: dict) -> str:
        try:
            earfcn = int(cell.get('earfcn', ''))
            band   = int(cell.get('band', ''))
            if _is_nr_arfcn(earfcn):
                return f'{_nrarfcn_to_mhz(earfcn):.0f}'
            f = _earfcn_to_mhz(band, earfcn)
            return f'{f:.0f}' if f is not None else '—'
        except (ValueError, TypeError):
            return '—'

    lines.append(
        f'  {BOLD}'
        f'{"T":<3}{"Band":<6}{"EARFCN":<9}{"PCI":<6}'
        f'{"Freq":>7}{"RSRP":>7}{"RSRQ":>7}{"SINR":>7}'
        f'  {"Seen":>8}'
        f'{RESET}'
    )
    lines.append(f'  {DIM}{"─"*3}{"─"*6}{"─"*9}{"─"*6}{"─"*7}{"─"*7}{"─"*7}{"─"*7}  {"─"*8}{RESET}')

    if not seen:
        lines.append(f'  {DIM}no neighbors yet{RESET}')
    else:
        for key, cell in seen.items():
            rsrp_n = int(cell['rsrp']) if cell.get('rsrp', '') not in ('', None) else None
            rsrq_n = int(cell['rsrq']) if cell.get('rsrq', '') not in ('', None) else None
            sinr_n = int(cell['sinr']) if cell.get('sinr', '') not in ('', None) else None
            serving = _nb_is_serving(cell)
            ago = _ago(cell['_ts'])
            try:
                nr = _is_nr_arfcn(int(cell.get('earfcn', 0)))
            except (ValueError, TypeError):
                nr = False
            t_label = f'{"NR" if nr else "L":<3}'
            pci_str = _val(cell.get('pci')) + ('*' if serving else ' ')
            freq_str = _nb_freq(cell)
            lines.append(
                f'  '
                f'{DIM if not serving else BOLD}{t_label}{RESET}'
                f'{_val(cell.get("band")):<6}'
                f'{_val(cell.get("earfcn")):<9}'
                f'{CYAN if serving else ""}{pci_str:<6}{RESET}'
                f'{freq_str:>7}'
                f'{_rsrp_color(rsrp_n)}{_val(cell.get("rsrp")):>7}{RESET}'
                f'{_rsrq_color(rsrq_n)}{_val(cell.get("rsrq")):>7}{RESET}'
                f'{_snr_color(sinr_n)}{_val(cell.get("sinr")):>7}{RESET}'
                f'  {DIM}{ago:>8}{RESET}'
            )

    lines.append('')
    lines.append(f'  {DIM}Updated {_ago(last_ok)} ago · interval 5s · fw {stats.get("wa_inner_version","")}{RESET}')
    return '\n'.join(lines)


# ── main loop ─────────────────────────────────────────────────────────────────

NEIGHBOR_TTL = 60  # seconds

def run(host: Optional[str] = None, username: Optional[str] = None,
        password: Optional[str] = None, interval: int = 5):
    mon = ZTEF50Monitor(host, username, password)
    seen: OrderedDict[str, dict] = OrderedDict()
    last_ok = time.time()
    last_stats: Dict[str, Any] = {}
    error: Optional[str] = None
    failures = 0
    backoff = interval

    try:
        while True:
            data = mon.fetch()
            now_ts = time.time()

            if data is None:
                failures += 1
                error = f'fetch failed ({failures}× in a row)'
                # exponential backoff: 5s → 10s → 20s → … → 60s max
                # avoids hammering login endpoint while device reboots
                backoff = min(interval * (2 ** (failures - 1)), 60)
                mon._new_session()
                mon.logged_in = False
            else:
                failures = 0
                backoff = interval
                error = None
                last_ok = now_ts
                last_stats = data

                # merge neighbors into seen-dict keyed by (band,pci)
                nb = data.get('neighbor_cell_info') or []
                if isinstance(nb, list):
                    for cell in nb:
                        key = f'B{cell.get("band","?")} PCI{cell.get("pci","?")}'
                        seen[key] = {**cell, '_ts': now_ts}

            # evict stale entries
            cutoff = now_ts - NEIGHBOR_TTL
            stale = [k for k, v in seen.items() if v['_ts'] < cutoff]
            for k in stale:
                del seen[k]

            # sort by rsrp desc
            sorted_seen: OrderedDict = OrderedDict(
                sorted(seen.items(),
                       key=lambda kv: int(kv[1].get('rsrp') or -999),
                       reverse=True))

            _clear()
            print(render(last_stats, sorted_seen, last_ok, error))
            sys.stdout.flush()

            time.sleep(backoff)

    except KeyboardInterrupt:
        _clear()
        mon._logout()
        print('Bye.')


def main():
    run()

if __name__ == '__main__':
    main()

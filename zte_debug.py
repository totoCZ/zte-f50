#!/usr/bin/env python3
"""
Debug script to fetch and print raw modem stats.
"""
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

# Import the monitor class from the main script
# In a real package, this would be a proper import
# For this single-file context, we duplicate necessary imports or exec
# But here we can just import the class if running in the same dir
try:
    from zte_f50_monitor import ZTEF50Monitor
except ImportError:
    print("Could not import ZTEF50Monitor. Make sure zte_f50_monitor.py is in the same directory.")
    sys.exit(1)

def main():
    load_dotenv(Path(__file__).parent / '.env')
    
    print("Initializing monitor...")
    mon = ZTEF50Monitor()
    
    print("Fetching data...")
    data = mon.fetch()
    
    if data:
        print("\n--- RAW DATA ---")
        print(json.dumps(data, indent=2))
        print("----------------\n")
        
        # Quick summary for verification
        print(f"Network Type: {data.get('network_type')}")
        print(f"LTE RSRP: {data.get('lte_rsrp')}")
        print(f"NR RSRP: {data.get('nr_rsrp')}")
        print(f"NR Band: {data.get('Nr_bands')}")
    else:
        print("Failed to fetch data. Check connection or credentials.")

if __name__ == '__main__':
    main()

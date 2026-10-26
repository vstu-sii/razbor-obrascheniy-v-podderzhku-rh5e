"""Compatibility entry point for versioned live-evidence checks."""
import json
from check_reviewed import check

if __name__ == '__main__':
    print(json.dumps(check()))

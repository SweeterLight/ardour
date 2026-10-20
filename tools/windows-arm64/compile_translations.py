#!/usr/bin/env python3
"""Compile existing translation catalogs and fail on any gettext error."""
from pathlib import Path
import subprocess


def main():
    repo = Path(__file__).resolve().parents[2]
    count = 0
    for component in ('gtk2_ardour', 'gtk2_ardour/appdata', 'libs/ardour',
                      'libs/gtkmm2ext', 'libs/tk/ytk'):
        catalogs = sorted((repo / component / 'po').glob('*.po'))
        if not catalogs and component != 'gtk2_ardour/appdata':
            raise RuntimeError(f'Translation sources are missing: {component}')
        for source in catalogs:
            subprocess.run(['msgfmt', '-c', '-f', '-o', source.with_suffix('.mo'), source], check=True)
            count += 1
    print(f'Compiled {count} translation catalogs without updating their sources.')


if __name__ == '__main__':
    main()

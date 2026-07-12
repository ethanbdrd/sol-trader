"""Helpers d'affichage console (colorama)."""

import sys
from colorama import init, Fore, Back, Style

# strip=False + convert=False en CI (pas de TTY) pour eviter les crashes
# colorama detecte automatiquement si stdout est un terminal
IS_TTY = sys.stdout.isatty()
init(autoreset=True, strip=not IS_TTY, convert=False)

W   = Style.BRIGHT + Fore.WHITE
DIM = Style.DIM + Fore.WHITE
G   = Style.BRIGHT + Fore.GREEN
R   = Style.BRIGHT + Fore.RED
Y   = Style.BRIGHT + Fore.YELLOW
C   = Style.BRIGHT + Fore.CYAN
M   = Style.BRIGHT + Fore.MAGENTA
RST = Style.RESET_ALL


def header(text):
    w = 62
    print()
    print(C + "┌" + "─" * (w-2) + "┐")
    print(C + "│" + W + f"  {text:<{w-4}}" + C + "│")
    print(C + "└" + "─" * (w-2) + "┘" + RST)


def section(text):
    print()
    print(C + f"  ── {text} " + DIM + "─" * max(0, 52 - len(text)) + RST)


def row(label, value, color=W, hint=""):
    label_str = DIM + f"  {label:<30}" + RST
    hint_str  = DIM + f"  {hint}" if hint else ""
    print(f"{label_str}{color}{value}{RST}{hint_str}")


def signal_row(label, verdict, color, direction=None, hint=""):
    dir_str = ""
    if direction == "long":
        dir_str = G + " ▲ LONG"
    elif direction == "short":
        dir_str = R + " ▼ SHORT"
    label_str = DIM + f"  {label:<30}" + RST
    hint_str  = DIM + f"   {hint}" if hint else ""
    print(f"{label_str}{color}{verdict}{dir_str}{RST}{hint_str}")


def verdict_box(label, color, bg=None):
    w = 60
    pad = (w - len(label) - 4) // 2
    line = " " * pad + f"  {label}  " + " " * pad
    # Background colors suppressed in non-TTY (CI) to avoid encoding issues
    if bg and IS_TTY:
        print(bg + color + Style.BRIGHT + f"\n  {line}\n" + RST)
    else:
        print(color + Style.BRIGHT + f"\n  {'='*(w-4)}")
        print(f"  {line[:w-4]}")
        print(f"  {'='*(w-4)}" + RST)

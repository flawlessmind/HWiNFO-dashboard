#!/usr/bin/env python3
import csv
import io
import re
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

from flask import Flask, render_template, send_from_directory

import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ----------------------------- НАСТРОЙКИ ----------------------------------
INPUT_FILE = r"C:\Users\rhine\OneDrive\Рабочий стол\analyzer_hwinfo\123.CSV"
PORT = 8000
# --------------------------------------------------------------------------

DATE_RE = re.compile(r'^\d{2}\.\d{2}\.\d{4}$')
YES, NO = {'да', 'yes'}, {'нет', 'no'}


def parse_header(raw):
    raw = raw.rstrip('\r\n').lstrip('\ufeff')
    inner = raw[1:].replace('""', '"') if raw.startswith('"') and '""' in raw else raw
    fields = next(csv.reader(io.StringIO(inner)))
    if fields and fields[0] == 'Date,Time':
        fields = ['Date', 'Time'] + fields[1:]
    return fields


def find_col(header, name, occurrence=0):
    idx = -1
    for _ in range(occurrence + 1):
        try:
            idx = header.index(name, idx + 1)
        except ValueError:
            return None
    return idx


def to_float(s):
    s = s.strip()
    if not s:
        return None
    try:
        return float(s.replace(',', '.'))
    except ValueError:
        return None


def parse_dump(filepath):
    path = Path(filepath)
    if not path.exists():
        sys.exit(f"Файл не найден: {filepath}")

    lines = path.read_text(encoding='utf-8-sig').splitlines()
    header = parse_header(lines[0])
    n = len(header)
    timestamps = []
    values = {i: [] for i in range(n)}
    yes, no = {i: 0 for i in range(n)}, {i: 0 for i in range(n)}
    data_rows, skipped = 0, 0

    for line in lines[1:]:
        s = line.strip()
        if not s or s.startswith('"Date,Time') or s.startswith('Date,Time,') or s.startswith(',,'):
            skipped += 1
            continue

        row = next(csv.reader([s]))
        if not row or not DATE_RE.match(row[0].strip()):
            skipped += 1
            continue

        try:
            ts = datetime.strptime(f"{row[0].strip()} {row[1].strip()}", "%d.%m.%Y %H:%M:%S.%f")
        except (ValueError, IndexError):
            skipped += 1
            continue

        timestamps.append(ts)
        data_rows += 1

        for i in range(n):
            if i >= len(row):
                values[i].append(None)
                continue
            v = row[i].strip()
            f = to_float(v) if v else None
            values[i].append(f)
            if f is None and v:
                if v.lower() in YES:
                    yes[i] += 1
                elif v.lower() in NO:
                    no[i] += 1

    return {'header': header, 'timestamps': timestamps, 'values': values,
            'yes': yes, 'no': no, 'data_rows': data_rows, 'skipped': skipped, 'path': path}


def save_summary_csv(r, out_path):
    header, values = r['header'], r['values']
    rows_for_html = []

    def num(x):
        return f'{x:.4f}'.replace('.', ',')

    with open(out_path, 'w', encoding='utf-8-sig', newline='') as f:
        f.write(f"# Total data rows: {r['data_rows']}\n")
        f.write(f"# Skipped lines: {r['skipped']}\n")
        f.write(f"# Source: {r['path']}\n")

        w = csv.writer(f, delimiter=';')
        w.writerow(['Parameter', 'Type', 'Mean', 'Min', 'Max', 'Std', 'Yes', 'No'])

        for i, name in enumerate(header):
            if name in ('Date', 'Time'):
                continue
            vals = [v for v in values[i] if v is not None]
            if vals:
                m, mn, mx = mean(vals), min(vals), max(vals)
                sd = stdev(vals) if len(vals) > 1 else 0.0
                w.writerow([name, 'numeric', num(m), num(mn), num(mx), num(sd), '', ''])
                rows_for_html.append({
                    'name': name, 'mean': f"{m:.2f}", 'min': f"{mn:.2f}",
                    'max': f"{mx:.2f}", 'std': f"{sd:.2f}", 'yes': '', 'no': ''
                })
            elif r['yes'][i] or r['no'][i]:
                w.writerow([name, 'bool', '', '', '', '', r['yes'][i], r['no'][i]])
                rows_for_html.append({
                    'name': name, 'mean': '', 'min': '', 'max': '', 'std': '',
                    'yes': r['yes'][i], 'no': r['no'][i]
                })
    return rows_for_html


def generate_charts(r, plots_dir):
    header, ts, values = r['header'], r['timestamps'], r['values']
    nan = float('nan')
    charts = []

    def get_ys(name, occ=0):
        idx = find_col(header, name, occ)
        return None if idx is None else [nan if v is None else v for v in values[idx]]

    def render(fig, title, filename):
        fig.savefig(plots_dir / filename, dpi=120)
        plt.close(fig)
        charts.append({'title': title, 'file': filename})

    def plot_single(title, col, ylabel, color, filename, occ=0):
        ys = get_ys(col, occ)
        if not ys or not any(v == v for v in ys): return
        fig, ax = plt.subplots(figsize=(10, 3.5))
        ax.plot(ts, ys, color=color, linewidth=1.0)
        ax.set_title(title, loc='left')
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        fig.autofmt_xdate(rotation=30)
        fig.tight_layout()
        render(fig, title, filename)

    def plot_dual(title, col1, col2, ylabel, c1, c2, filename):
        y1, y2 = get_ys(col1), get_ys(col2)
        if not y1 or not y2 or not any(v == v for v in y1 + y2): return
        fig, ax = plt.subplots(figsize=(10, 3.5))
        ax.plot(ts, y1, color=c1, linewidth=1.0, label=col1.split(' [')[0])
        ax.plot(ts, y2, color=c2, linewidth=1.0, label=col2.split(' [')[0])
        ax.set_title(title, loc='left')
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.3)
        ax.legend(loc='upper right')
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        fig.autofmt_xdate(rotation=30)
        fig.tight_layout()
        render(fig, title, filename)

    # Обычные графики
    plot_single("Температура ЦП", "ЦП (Tctl/Tdie) [°C]", "°C", "tab:blue", "cpu_temp.png")
    plot_single("Частота ЦП", "Частоты ядер (avg) [МГц]", "МГц", "tab:cyan", "cpu_freq.png")
    plot_single("Потребление ЦП", "Полная потребляемая мощность ЦП [W]", "Вт", "darkorange", "cpu_power.png")

    plot_single("Температура ГП", "Температура ГП [°C]", "°C", "tab:red", "gpu_temp.png", occ=1)
    plot_single("Частота ГП", "Частота ГП [МГц]", "МГц", "tab:orange", "gpu_freq.png", occ=1)
    plot_single("Потребление ГП", "GPU Потребляемая мощность [W]", "Вт", "crimson", "gpu_power.png")
    plot_single("Видеопамять (VRAM)", "Выделенная память ГП [МБ]", "МБ", "tab:green", "vram.png")
    plot_single("Температура видеопамяти", "Память ГП Chip Температура (avg) [°C]", "°C", "tab:purple", "vram_temp.png")
    plot_single("Использование ОЗУ", "Используемая физическая память [МБ]", "МБ", "seagreen", "ram.png")

    # Двойные графики
    plot_dual("Вентиляторы ГП", "GPU Вентилятор1 [%]", "GPU Вентилятор2 [%]", "%", "tab:blue", "tab:orange",
              "gpu_fans.png")
    plot_dual("GPU Busy vs CPU Busy", "GPU Busy (avg) [ms]", "ЦП Busy (avg) [ms]", "ms", "tab:purple", "tab:green",
              "busy.png")

    # FPS + Загрузка (Двойная ось)
    i_fps = find_col(header, "Частота кадров представлена (avg) [FPS]")
    i_gpu = find_col(header, "Использование D3D ГП [%]", 1)
    i_cpu = find_col(header, "Общая загрузка ЦП [%]")
    if i_fps is not None and (i_gpu is not None or i_cpu is not None):
        y_fps = get_ys("Частота кадров представлена (avg) [FPS]")
        if y_fps and any(v == v for v in y_fps):
            fig, ax1 = plt.subplots(figsize=(10, 3.5))
            ax1.plot(ts, y_fps, color='royalblue', label='FPS (avg)')
            ax1.set_ylabel('FPS', color='royalblue')
            ax1.tick_params(axis='y', labelcolor='royalblue')
            ax1.grid(True, alpha=0.3)

            ax2 = ax1.twinx()
            if i_gpu is not None: ax2.plot(ts, get_ys("Использование D3D ГП [%]", 1), color='tab:red', alpha=0.7,
                                           label='Загрузка ГП [%]')
            if i_cpu is not None: ax2.plot(ts, get_ys("Общая загрузка ЦП [%]"), color='tab:orange', alpha=0.7,
                                           label='Загрузка ЦП [%]')

            ax2.set_ylabel('Загрузка [%]')
            ax2.set_ylim(0, 105)
            h1, l1 = ax1.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax1.legend(h1 + h2, l1 + l2, loc='upper right')
            ax1.set_title("FPS + Загрузка", loc='left')
            ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
            fig.autofmt_xdate(rotation=30)
            fig.tight_layout()
            render(fig, "FPS + Загрузка", "fps_load.png")

    return charts


def main():
    print("Парсинг файла...")
    result = parse_dump(INPUT_FILE)

    # Создаем папку с именем дампа (123/plots/)
    out_dir = Path(INPUT_FILE).with_suffix('')
    plots_dir = out_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    print(f"Сохранение данных в {out_dir}")
    table_rows = save_summary_csv(result, out_dir / "summary.csv")

    print("Генерация графиков...")
    charts = generate_charts(result, plots_dir)

    app = Flask(__name__)

    @app.route('/')
    def index():
        return render_template('index.html',
                               filename=result['path'].name,
                               data_rows=result['data_rows'],
                               skipped=result['skipped'],
                               charts=charts,
                               table_rows=table_rows)

    @app.route('/plots/<filename>')
    def serve_plot(filename):
        return send_from_directory(plots_dir, filename)

    @app.route('/summary.csv')
    def serve_csv():
        return send_from_directory(out_dir, 'summary.csv', as_attachment=True)

    print(f"Запуск локального сервера на http://127.0.0.1:{PORT}")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == '__main__':
    main()
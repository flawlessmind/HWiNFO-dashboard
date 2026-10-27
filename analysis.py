import argparse
import csv
import io
import json
import re
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev

from flask import Flask, Response, render_template

DATE_RE = re.compile(r'^\d{2}\.\d{2}\.\d{4}$')
YES, NO = {'да', 'yes'}, {'нет', 'no'}


def categorize(name):
    n = name.lower()
    if 'троттлинг' in n or 'throttling' in n:
        return "Троттлинг"
    if 'ошибка' in n or 'error' in n or 'сбой' in n or 'whea' in n:
        return "Ошибки"
    if 'предел' in n or 'limit' in n or 'ограничени' in n:
        return "Пределы"
    if ('fps' in n or 'frame time' in n or 'presentmon' in n or 'busy' in n
            or 'wait' in n or 'animation' in n):
        return "Производительность"
    if 'диск' in n or 'drive' in n or 's.m.a.r.t' in n or 'ssd' in n or 'nvme' in n:
        return "Диски"
    if 'сеть' in n or 'network' in n or 'ethernet' in n:
        return "Сеть"
    if 'гп' in n or 'gpu' in n or 'd3d' in n or 'vram' in n:
        return "Видеокарта"
    if ('материнская' in n or 'vcore' in n or 'vrm' in n or 'chipset' in n
            or 'vin' in n or 'dimm' in n):
        return "Материнская плата"
    if 'цп' in n or 'cpu' in n or 'core' in n or 'c-state' in n:
        return "Процессор"
    if 'память' in n or 'memory' in n or 'ram' in n or 'page file' in n:
        return "Память"
    return "Другое"


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
    raw_text = path.read_text(encoding='utf-8-sig')
    lines = raw_text.splitlines()
    if not lines:
        raise ValueError('CSV-файл пуст.')
    header = parse_header(lines[0])
    n = len(header)
    timestamps, values = [], {i: [] for i in range(n)}
    yes, no = {i: 0 for i in range(n)}, {i: 0 for i in range(n)}
    data_rows = 0

    for line in lines[1:]:
        s = line.strip()
        if not s or s.startswith('"Date,Time') or s.startswith('Date,Time,') or s.startswith(',,'):
            continue

        try:
            row = next(csv.reader([s]))
        except (csv.Error, StopIteration):
            continue
        if not row or not DATE_RE.match(row[0].strip()):
            continue

        try:
            ts = datetime.strptime(
                f"{row[0].strip()} {row[1].strip()}",
                "%d.%m.%Y %H:%M:%S.%f",
            )
        except (ValueError, IndexError):
            try:
                ts = datetime.strptime(
                    f"{row[0].strip()} {row[1].strip()}",
                    "%d.%m.%Y %H:%M:%S",
                )
            except (ValueError, IndexError):
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

    duration_str = "0 сек"
    if timestamps:
        delta = timestamps[-1] - timestamps[0]
        total = int(delta.total_seconds())
        h, m, s = total // 3600, (total % 3600) // 60, total % 60
        duration_str = f"{h} ч {m:02d} мин" if h else (f"{m} мин {s:02d} сек" if m else f"{s} сек")

    return {
        'header': header,
        'timestamps': timestamps,
        'values': values,
        'yes': yes,
        'no': no,
        'data_rows': data_rows,
        'path': path,
        'duration': duration_str,
    }


def compute_summary(r):
    rows = []
    for i, name in enumerate(r['header']):
        if name in ('Date', 'Time'):
            continue
        cat = categorize(name)
        vals = [v for v in r['values'][i] if v is not None]
        if vals:
            m, mn, mx = mean(vals), min(vals), max(vals)
            sd = stdev(vals) if len(vals) > 1 else 0.0
            rows.append({
                'cat': cat, 'name': name, 'type': 'numeric',
                'mean': f"{m:.2f}", 'min': f"{mn:.2f}",
                'max': f"{mx:.2f}", 'std': f"{sd:.2f}",
                'yes': '', 'no': '', '_m': m, '_mn': mn, '_mx': mx, '_sd': sd,
            })
        elif r['yes'][i] or r['no'][i]:
            rows.append({
                'cat': cat, 'name': name, 'type': 'bool',
                'mean': '', 'min': '', 'max': '', 'std': '',
                'yes': r['yes'][i], 'no': r['no'][i],
                '_yes': r['yes'][i], '_no': r['no'][i],
            })
    return rows


def build_system_summary(r, table_rows):
    h, v = r['header'], r['values']

    def get_stats(name, occ=0):
        idx = find_col(h, name, occ)
        if idx is not None:
            vals = [x for x in v[idx] if x is not None]
            if vals:
                return mean(vals), max(vals)
        return None, None

    cpu_load = get_stats("Общая загрузка ЦП [%]")
    cpu_temp = get_stats("ЦП (Tctl/Tdie) [°C]")
    cpu_power = get_stats("Полная потребляемая мощность ЦП [W]")

    gpu_load = get_stats("Использование D3D ГП [%]", 1)
    gpu_temp = get_stats("Температура ГП [°C]", 1)
    gpu_power = get_stats("GPU Потребляемая мощность [W]")

    def get_memory_stats(candidates):
        """Find the first available memory-use sensor among known HWiNFO labels."""
        for name in candidates:
            stats = get_stats(name)
            if stats[0] is not None:
                return stats, name
        # Fall back to a case-insensitive exact header match for dumps with different casing.
        by_lower = {name.lower(): name for name in h}
        for name in candidates:
            actual = by_lower.get(name.lower())
            if actual is not None:
                stats = get_stats(actual)
                if stats[0] is not None:
                    return stats, actual
        return (None, None), None

    vram_stats, vram_column = get_memory_stats([
        "Выделенная память ГП [МБ]",
        "Выделенная память ГП [МБ] ",
        "Dedicated GPU Memory [MB]",
        "GPU Dedicated Memory [MB]",
        "GPU Memory Allocated [MB]",
        "Использование видеопамяти [МБ]",
    ])
    ram_stats, ram_column = get_memory_stats([
        "Используемая физическая память [МБ]",
        "Используемая физическая память [MB]",
        "Used Physical Memory [MB]",
        "Physical Memory Used [MB]",
        "Физическая память: Используется [МБ]",
    ])

    def count_yes(cat_name):
        return sum(row.get('_yes', 0) for row in table_rows
                   if row['cat'] == cat_name and row.get('type') == 'bool')

    thr_count = count_yes("Троттлинг")
    err_count = count_yes("Ошибки")
    lines = []

    def fmt(mean_val, max_val, unit):
        return f"ср. <b>{mean_val:.1f}{unit}</b> (макс {max_val:.1f}{unit})"

    if cpu_load[0] is not None:
        parts = [f"загрузка {fmt(cpu_load[0], cpu_load[1], '%')}"]
        if cpu_temp[0] is not None:
            parts.append(f"температура {fmt(cpu_temp[0], cpu_temp[1], '°C')}")
        if cpu_power[0] is not None:
            parts.append(f"потребление {fmt(cpu_power[0], cpu_power[1], ' Вт')}")
        lines.append("Процессор: " + ", ".join(parts) + ".")

    if gpu_load[0] is not None:
        parts = [f"загрузка {fmt(gpu_load[0], gpu_load[1], '%')}"]
        if gpu_temp[0] is not None:
            parts.append(f"температура {fmt(gpu_temp[0], gpu_temp[1], '°C')}")
        if gpu_power[0] is not None:
            parts.append(f"потребление {fmt(gpu_power[0], gpu_power[1], ' Вт')}")
        lines.append("Видеокарта: " + ", ".join(parts) + ".")

    def memory_unit(column_name):
        match = re.search(r"\[([^]]+)\]", column_name or '')
        return match.group(1) if match else 'МБ'

    if vram_stats[0] is not None:
        unit = memory_unit(vram_column)
        lines.append(
            f"Видеопамять: использование {fmt(vram_stats[0], vram_stats[1], ' ' + unit)}."
        )
    if ram_stats[0] is not None:
        unit = memory_unit(ram_column)
        lines.append(
            f"Оперативная память: использование {fmt(ram_stats[0], ram_stats[1], ' ' + unit)}."
        )

    if thr_count == 0:
        lines.append("Троттлинг: <b>не обнаружен</b>.")
    else:
        lines.append(
            f"Троттлинг: <b><span style='color:#e53e3e'>обнаружен</span></b> "
            f"(зафиксировано {thr_count} срабатываний)."
        )

    if err_count == 0:
        lines.append("Ошибки: <b>не обнаружены</b>.")
    else:
        lines.append(
            f"Ошибки: <b><span style='color:#e53e3e'>обнаружены</span></b> "
            f"(зафиксировано {err_count} событий)."
        )

    return lines


def build_charts(r):
    header, values = r['header'], r['values']
    ts_ms = [int(t.timestamp() * 1000) for t in r['timestamps']]
    charts = []

    def stats_str(vals):
        valid = [v for v in vals if v is not None]
        return f"Ср: {mean(valid):.1f}, Макс: {max(valid):.1f}" if valid else ''

    def add_chart(category, title, traces_spec, ylabel, y2label=None):
        traces, any_valid = [], False
        for col, occ, color, yax in traces_spec:
            idx = find_col(header, col, occ)
            if idx is None or not any(v is not None for v in values[idx]):
                continue
            any_valid = True
            traces.append({
                'name': col.split(' [')[0], 'data': values[idx],
                'color': color, 'yaxis': yax, 'stats': stats_str(values[idx]),
            })
        if not any_valid:
            return
        charts.append({
            'id': f'chart-{len(charts)}', 'title': title, 'category': category,
            'traces': traces, 'ylabel': ylabel, 'y2label': y2label,
            'dual_axis': y2label is not None,
        })

    # Процессор
    add_chart("Процессор", "Температура ЦП", [("ЦП (Tctl/Tdie) [°C]", 0, "#1f77b4", "y")], "°C")
    add_chart("Процессор", "Частота ЦП", [("Частоты ядер (avg) [МГц]", 0, "#17becf", "y")], "МГц")
    add_chart("Процессор", "Общая загрузка ЦП", [("Общая загрузка ЦП [%]", 0, "#ff6347", "y")], "%")
    add_chart("Процессор", "Потребление ЦП", [("Полная потребляемая мощность ЦП [W]", 0, "#ff8c00", "y")], "Вт")

    # Видеокарта
    add_chart("Видеокарта", "Температура ГП", [("Температура ГП [°C]", 1, "#d62728", "y")], "°C")
    add_chart("Видеокарта", "Частота ГП", [("Частота ГП [МГц]", 1, "#ff7f0e", "y")], "МГц")
    add_chart("Видеокарта", "Потребление ГП", [("GPU Потребляемая мощность [W]", 0, "#dc143c", "y")], "Вт")
    add_chart("Видеокарта", "Видеопамять (VRAM)", [("Выделенная память ГП [МБ]", 0, "#2ca02c", "y")], "МБ")
    add_chart("Видеокарта", "Температура видеопамяти", [("Память ГП Chip Температура (avg) [°C]", 0, "#9467bd", "y")], "°C")
    add_chart("Видеокарта", "Вентиляторы ГП", [("GPU Вентилятор1 [%]", 0, "#1f77b4", "y"), ("GPU Вентилятор2 [%]", 0, "#ff7f0e", "y")], "%")

    # Производительность
    add_chart("Производительность", "FPS (avg) vs FPS 1% low", [("Частота кадров представлена (avg) [FPS]", 0, "#4169e1", "y"), ("Частота кадров представлена (1% low) [FPS]", 0, "#ff6347", "y")], "FPS")
    add_chart("Производительность", "GPU Busy vs CPU Busy", [("GPU Busy (avg) [ms]", 0, "#9467bd", "y"), ("ЦП Busy (avg) [ms]", 0, "#2ca02c", "y")], "ms")
    add_chart("Производительность", "FPS + Загрузка", [("Частота кадров представлена (avg) [FPS]", 0, "#4169e1", "y"), ("Использование D3D ГП [%]", 1, "#d62728", "y2"), ("Общая загрузка ЦП [%]", 0, "#ff8c00", "y2")], "FPS", "Загрузка [%]")

    # Память / Материнская плата
    add_chart("Память", "Использование ОЗУ", [("Используемая физическая память [МБ]", 0, "#2e8b57", "y")], "МБ")
    add_chart("Материнская плата", "Температура VRM", [("ЦП VDDCR_VDD VRM (SVI3 TFN) [°C]", 0, "#a52a2a", "y")], "°C")

    return charts, ts_ms


def main():
    parser = argparse.ArgumentParser(description="HWiNFO CSV dashboard")
    parser.add_argument('csv', help='Путь к CSV-файлу HWiNFO')
    parser.add_argument('--port', type=int, default=8000, help='Порт (по умолчанию 8000)')
    args = parser.parse_args()

    if not Path(args.csv).exists():
        print(f"Файл не найден: {args.csv}")
        return

    print(f"Парсинг {args.csv}...")
    result = parse_dump(args.csv)
    print("Подсчёт статистики...")
    table_rows = compute_summary(result)
    system_summary = build_system_summary(result, table_rows)
    print("Подготовка графиков...")
    charts, ts_ms = build_charts(result)

    categories_data = {}
    for row in table_rows:
        categories_data.setdefault(row['cat'], {'table': [], 'charts': []})['table'].append(row)
    for chart in charts:
        categories_data.setdefault(chart['category'], {'table': [], 'charts': []})['charts'].append(chart)

    order = ["Процессор", "Видеокарта", "Производительность", "Память",
             "Материнская плата", "Диски", "Сеть", "Троттлинг",
             "Ошибки", "Пределы", "Другое"]
    sorted_categories = {k: categories_data[k] for k in order if k in categories_data}

    app = Flask(__name__)

    @app.route('/')
    def index():
        return render_template(
            'index.html',
            filename=result['path'].name,
            data_rows=result['data_rows'],
            duration=result['duration'],
            categories=sorted_categories,
            system_summary=system_summary,
            charts_json=json.dumps(charts, ensure_ascii=False),
            ts_json=json.dumps(ts_ms),
        )

    @app.route('/summary.csv')
    def serve_csv():
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=';')
        w.writerow(['Категория', 'Parameter', 'Type', 'Mean', 'Min', 'Max', 'Std', 'Yes', 'No'])

        def num(x):
            return f'{x:.4f}'.replace('.', ',')

        for row in table_rows:
            if row['type'] == 'numeric':
                w.writerow([row['cat'], row['name'], 'numeric', num(row['_m']),
                            num(row['_mn']), num(row['_mx']), num(row['_sd']), '', ''])
            else:
                w.writerow([row['cat'], row['name'], 'bool', '', '', '', '',
                            row['_yes'], row['_no']])
        return Response(
            buf.getvalue().encode('utf-8-sig'),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename="summary.csv"'},
        )

    print(f"Запуск: http://127.0.0.1:{args.port}")
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == '__main__':
    main()

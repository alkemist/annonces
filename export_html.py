"""Génère un rapport HTML autonome (sans serveur) avec des onglets contenant soit une image,
soit un tableau Tabulator.

La configuration est un dictionnaire dont les clés sont les noms d'onglets. Un onglet "feuille"
est un dictionnaire contenant au moins "path" :

  {
    "Déploiements": {
      "path": "output/versions_deployed_by_server.csv",
      "groups": ["server", "env"],
      "highlight_error_columns": ["error_front", "error_back"],
      "default_filters": {"server": "build_1"},
      "default_columns_hidden": ["url"],
      "columns_select": ["server", "env", "module"],
      "columns_number": ["version_major"],
      "columns_nowrap": ["version_front"],
    },
    "Schéma": {
      "type": "image",
      "path": "output/schema_deploiement.png",
    },
    "Tickets": {
      "Par version": {"path": "output/tickets_by_version.csv"},
      "Par module": {"path": "output/versions_published_by_module.csv"},
    },
  }

Une valeur non feuille (un dictionnaire dont les valeurs sont elles-mêmes des dictionnaires sans
"path") crée un groupe d'onglets imbriqué : chaque clé devient un sous-onglet, à n'importe quelle
profondeur.

Utilisation :
    from export_table import export_table_html
    export_table_html(config, "output/rapport.html", title="Rapport Synapse")
"""

import csv
import html
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _title_from_file_mtime(path, prefix: str = "Rapport au") -> str:
    """Construit un titre de type "Rapport au 13/10 à 01:00" à partir de la date de
    dernière modification du fichier indiqué.

    :param path: chemin du fichier dont on lit la date de modification.
    :param prefix: préfixe du titre généré.
    :return: le titre formaté.
    """
    file_path = Path(path)
    mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
    return f"{prefix} {mtime.strftime('%d/%m')} à {mtime.strftime('%H:%M')}"

TABULATOR_VERSION = "6.5.0"
TABULATOR_CSS = f"https://unpkg.com/tabulator-tables@{TABULATOR_VERSION}/dist/css/tabulator_modern.min.css"
TABULATOR_JS = f"https://unpkg.com/tabulator-tables@{TABULATOR_VERSION}/dist/js/tabulator.min.js"
FORKAWESOME_CSS = f"https://cdn.jsdelivr.net/npm/fork-awesome@1.2.0/css/fork-awesome.min.css"

STYLE = """
  :root {
    color-scheme: light;
    --bg: #f8f9fa;
    --panel: #ffffff;
    --border: #d7dee7;
    --text: #333;
    --muted: #5b6b7c;
    --accent: #1d6fd6;
    --accent-soft: #e3effd;
    --error-bg: rgba(220, 53, 69, 0.2);
    --error-bg-hover: rgba(220, 53, 69, 0.35);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: system-ui, -apple-system, sans-serif;
  }
  html, body, .tabs-group, .tabs-panels, .tab-panel, .panel-body { height: 100%; }
  .tabs-group {
      display: flex;
      flex-direction: column;
      background: #fff;
      border-radius: 0;
  }

  .tabs-bar { display: flex; flex-wrap: wrap; flex-shrink: 0; border-bottom: 2px solid #dee2e6; margin-bottom: 0; gap: 0.5rem; }
  .tab-btn {
      padding: 0.6rem 1.2rem;
      border: none;
      background: none;
      font-size: 0.8rem;
      font-weight: 500;
      color: #6c757d;
      cursor: pointer;
      border-bottom: 3px solid transparent;
      transition: all 0.2s ease;
  }
  .tab-btn:hover { color: #0d6efd; }
  .tab-btn.active { color: #0d6efd; border-bottom-color: #0d6efd; font-weight: 600; }
  .tab-panel { display: none; }
  .tab-panel.active {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
  }
  .image-box, .table-wrapper {
      flex: 1;
      min-height: 0;
      overflow: auto;
  }
  .image-box img {
      display: block;
      max-width: 100%;
      height: auto;
      margin: 0 auto;
  }

  .col-nowrap {
      white-space: nowrap !important;
  }

  table.dataTable tbody tr.has-error > * {
      background-color: rgba(220, 53, 69, 0.35) !important;
  }

  .col-toggle-bar {
      position: relative;
      display: flex;
      flex-shrink: 0;
      justify-content: flex-end;
      margin-bottom: 0.4rem;
  }
  .col-toggle-btn {
      padding: 0.35rem 0.8rem;
      font-size: 0.8rem;
      border: 1px solid #ced4da;
      border-radius: 0;
      background: #fff;
      color: #495057;
      cursor: pointer;
  }
  .col-toggle-btn:hover { background: #f1f3f5; }
  .col-toggle-menu {
      display: none;
      position: absolute;
      top: 100%;
      right: 0;
      margin-top: 4px;
      background: #fff;
      border: 1px solid #ced4da;
      border-radius: 0;
      box-shadow: 0 2px 8px rgba(0,0,0,0.12);
      padding: 0.5rem;
      z-index: 10;
      max-height: 300px;
      overflow-y: auto;
      min-width: 200px;
  }
  .col-toggle-menu.open { display: block; }
  .col-toggle-menu label {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      font-size: 0.82rem;
      padding: 0.15rem 0.3rem;
      white-space: nowrap;
      cursor: pointer;
  }
  .col-toggle-menu label:hover { background: #f1f3f5; }

  .tabulator-header-filter {
      position: relative;
  }
  .tabulator-header-filter input,
  .tabulator-header-filter .tabulator-edit-list {
      padding-right: 16px !important;
      box-sizing: border-box;
  }
  .header-filter-clear {
      position: absolute;
      top: 42%;
      right: 4px;
      transform: translateY(-50%);
      cursor: pointer;
      color: #99a3ad;
      font-size: 0.85rem;
      line-height: 1;
      padding: 0 2px;
      z-index: 20;
      user-select: none;
  }
  .header-filter-clear:hover { color: #dc3545; }

  .tabulator .tabulator-header {
    border-bottom: 0px;
    margin-bottom: 0px;
  }

  .image-box {
    display: flex;
    justify-content: center;
    align-items: center;
    min-height: 200px;
  }
  .image-box img {
    max-width: 100%;
    max-height: 82vh;
    border-radius: 0;
  }
  .tabulator {
    background-color: var(--bg);
    border: 1px solid var(--border);
    border-radius: 0;
    font-size: 0.88rem;
  }
  .tabulator-table {
    border-bottom: 1px solid #999;
    width: 100%;
  }
  .tabulator-row.error-row,
  .tabulator-row.error-row .tabulator-cell {
    background: var(--error-bg) !important;
  }
  .tabulator-row.error-row:hover,
  .tabulator-row.error-row:hover .tabulator-cell {
    background: var(--error-bg-hover) !important;
  }
  .tabulator .tabulator-cell.cell-nowrap {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .tabulator-row.tabulator-group {
    background: var(--accent-soft);
    font-weight: 700;
    color: var(--accent);
  }
  .tabulator-row:nth-child(2n) {
    background: #fff;
  }
  .tabulator-row:nth-child(2n) .tabulator-cell:first-child,
  .tabulator-row .tabulator-cell:first-child {
    border-left: none;
  }
  .tabulator .tabulator-header,
  .tabulator .tabulator-header .tabulator-col:first-child { padding-left: 0; }
"""

# Préfixes d'URL masqués à l'affichage (le lien complet reste utilisable via le href).
HIDDEN_URL_PREFIXES = [
    "https://gitlab.factory.maela.care/synapse",
    "https://maelacare.atlassian.net/browse/"
]

# Fonctions JavaScript partagées par toutes les tables, injectées une seule fois dans le document.
SHARED_JS = r"""
const HIDDEN_URL_PREFIXES = __HIDDEN_URL_PREFIXES__;

function escapeHtml(value) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function stripHiddenUrlPrefix(text) {
  for (const prefix of HIDDEN_URL_PREFIXES) {
    if (text.startsWith(prefix)) {
      return text.slice(prefix.length) || '/';
    }
  }
  return text;
}

function cellFormatter(cell) {
  const value = cell.getValue();
  if (value === null || value === undefined || value === '') {
    return '';
  }
  const text = String(value);
  if (/^(https?:\/\/|mailto:|ftp:\/\/|www\.)/i.test(text)) {
    const href = text.toLowerCase().startsWith('www.') ? 'https://' + text : text;
    const displayText = stripHiddenUrlPrefix(text);
    return '<a href="' + escapeHtml(href) + '" title="' + escapeHtml(text) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(displayText) + '</a>';
  }
  return escapeHtml(text);
}

function autocompleteHeaderFilterFunc(term, value, row, filterParams) {
  console.log('autocompleteHeaderFilterFunc:', String(term), '/', String(value),'/', String(row), filterParams, String(value).toLowerCase().includes(String(term).toLowerCase()));
  if (term === '' || term === null || term === undefined) {
    return true;
  }
  if (Array.isArray(value)) {
    return value.some(function (val) {
      return String(val).toLowerCase().includes(String(term).toLowerCase());
    });
  }
  return String(value).toLowerCase().includes(String(term).toLowerCase());
}

function numberHeaderFilterFunc(term, value, row, filterParams) {
  if (term === '' || term === null || term === undefined) {
    return true;
  }
  const match = String(term).trim().match(/^(<=|>=|<|>|=)?\s*(-?\d+(?:[.,]\d+)?)$/);
  if (!match) {
    return true;
  }
  const operator = match[1] || '=';
  const target = parseFloat(match[2].replace(',', '.'));
  value = parseFloat(String(value).replace(',', '.'));
  if (Number.isNaN(value)) {
    return false;
  }
  switch (operator) {
    case '<': return value < target;
    case '>': return value > target;
    case '<=': return value <= target;
    case '>=': return value >= target;
    default: return value === target;
  }
}

function addHeaderFilterClearButtons(table, fields) {
  (fields || []).forEach(function (info) {
    var column = table.getColumn(info.field);
    if (!column) {
      return;
    }
    var headerEl = column.getElement();
    var filterEl = headerEl && headerEl.querySelector('.tabulator-header-filter');
    if (!filterEl || filterEl.querySelector('.header-filter-clear')) {
      return;
    }
    var btn = document.createElement('span');
    btn.className = 'header-filter-clear';
    btn.title = 'Effacer le filtre';
    btn.innerHTML = '&times;';
    btn.addEventListener('mousedown', function (e) {
      e.preventDefault();
      e.stopPropagation();
      table.setHeaderFilterValue(info.field, info.multiselect ? [] : '');
    });
    filterEl.appendChild(btn);
  });
}

function wireTabGroups() {
  document.querySelectorAll('.tabs-group').forEach(function (group) {
    const buttons = group.querySelectorAll(':scope > .tabs-bar > .tab-btn');
    const panels = group.querySelectorAll(':scope > .tabs-panels > .tab-panel');
    buttons.forEach(function (btn) {
      btn.addEventListener('click', function () {
        buttons.forEach(function (b) { b.classList.toggle('active', b === btn); });
        panels.forEach(function (p) { p.classList.toggle('active', p.id === btn.dataset.target); });
      });
    });
  });
}

function makeRowFormatter(errorColumns) {
  return function (row) {
    if (!errorColumns || !errorColumns.length) {
      return;
    }
    const data = row.getData();
    const hasError = errorColumns.some(function (col) {
      return String(data[col] ?? '').includes('❌');
    });
    row.getElement().classList.toggle('error-row', hasError);
  };
}

var headerMenu = function(){
    var menu = [];
    var columns = this.getColumns();

    for(let column of columns){

        //create checkbox element using font awesome icons
        let icon = document.createElement("i");
        icon.classList.add("fa");
        icon.classList.add(column.isVisible() ? "fa-check-square" : "fa-square");

        //build label
        let label = document.createElement("span");
        let title = document.createElement("span");

        title.textContent = " " + column.getDefinition().title;

        label.appendChild(icon);
        label.appendChild(title);

        //create menu item
        menu.push({
            label:label,
            action:function(e){
                //prevent menu closing
                e.stopPropagation();

                //toggle current column visibility
                column.toggle();

                //change menu item icon
                if(column.isVisible()){
                    icon.classList.remove("fa-square");
                    icon.classList.add("fa-check-square");
                }else{
                    icon.classList.remove("fa-check-square");
                    icon.classList.add("fa-square");
                }
            }
        });
    }

    return menu;
};
"""


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _read_csv_rows(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Fichier CSV introuvable : {path}")

    with path.open(encoding="utf-8-sig", newline="") as source:
        sample = source.read(4096)
        source.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(source, dialect=dialect)
        rows = [dict(row) for row in reader]
        columns = list(reader.fieldnames or [])
    return columns, rows


def _is_leaf_config(node) -> bool:
    return isinstance(node, dict) and "path" in node


def _build_table_panel(tab_id: str, cfg: dict, base_dir: Path) -> str:
    csv_path = Path(cfg["path"])
    if not csv_path.is_absolute():
        csv_path = (base_dir / csv_path).resolve()

    columns, rows = _read_csv_rows(csv_path)

    groups = _as_list(cfg.get("groups"))
    highlight_error_columns = _as_list(cfg.get("highlight_error_columns"))
    default_filters = cfg.get("default_filters") or {}
    default_columns_hidden = set(_as_list(cfg.get("default_columns_hidden")))
    columns_select = set(_as_list(cfg.get("columns_select")))
    columns_multiselect = set(_as_list(cfg.get("columns_multiselect")))
    columns_number = set(_as_list(cfg.get("columns_number")))
    columns_nowrap = set(_as_list(cfg.get("columns_nowrap")))

    list_filter_fields = []

    column_defs = []
    for column in columns:
        column_def = {
            "title": column,
            "field": column,
            "headerFilterPlaceholder": "Filtrer...",
            "formatter": "__CELL_FORMATTER__",
            "visible": column not in default_columns_hidden,
            "headerMenu": "__HEADER_MENU__",
        }

        if column in columns_nowrap:
            column_def["cssClass"] = "cell-nowrap"

        if column in columns_select or column in columns_multiselect:
            is_multiselect = column in columns_multiselect
            column_def["headerFilter"] = "list"
            column_def["headerFilterParams"] = {
                "valuesLookup": True,
                "multiselect": is_multiselect,
                "clearable": True,
            }
            list_filter_fields.append({"field": column, "multiselect": is_multiselect})
        elif column in columns_number:
            column_def["headerFilter"] = "input"
            column_def["headerFilterFunc"] = "__NUMBER_FILTER__"
            column_def["headerFilterPlaceholder"] = "=, <, > valeur"
        else:
            column_def["headerFilter"] = "input"

        column_defs.append(column_def)

    initial_header_filter = [
        {
            "field": field,
            "value": _as_list(value) if field in columns_select else value,
        }
        for field, value in default_filters.items()
    ]

    options = {
        "height": "calc(100vh - 80px)",
        "layout": "fitDataFill",
        "placeholder": "Aucune donnée",
        "pagination": False,
        "movableColumns": True,
        "tooltips": True,
        "columns": column_defs,
        "groupBy": groups,
        "initialHeaderFilter": initial_header_filter,
        "rowFormatter": "__ROW_FORMATTER__",
        "data": rows,
    }

    options_json = json.dumps(options, ensure_ascii=False, indent=2)
    options_json = options_json.replace('"__CELL_FORMATTER__"', "cellFormatter")
    options_json = options_json.replace('"__NUMBER_FILTER__"', "numberHeaderFilterFunc")
    options_json = options_json.replace('"__AUTOCOMPLETE_FILTER__"', "autocompleteHeaderFilterFunc")
    options_json = options_json.replace('"__HEADER_MENU__"', "headerMenu")
    options_json = options_json.replace(
        '"__ROW_FORMATTER__"',
        f"makeRowFormatter({json.dumps(highlight_error_columns, ensure_ascii=False)})",
    )

    list_filter_fields_json = json.dumps(list_filter_fields, ensure_ascii=False)
    table_var = f"table_{tab_id}".replace("-", "_")

    return f"""
    <div class="table-box">
      <div id="{tab_id}-table"></div>
    </div>
    <script>
      (window.__tableQueue = window.__tableQueue || []).push(function () {{
        var {table_var} = new Tabulator('#{tab_id}-table', {options_json});
        {table_var}.on('tableBuilt', function () {{
          addHeaderFilterClearButtons({table_var}, {list_filter_fields_json});
        }});
        {table_var}.on('headerFilterChanged', function () {{
          addHeaderFilterClearButtons({table_var}, {list_filter_fields_json});
        }});
        {table_var}.on('dataFiltered', function () {{
          addHeaderFilterClearButtons({table_var}, {list_filter_fields_json});
        }});
      }});
    </script>
  """


def _build_image_panel(cfg: dict) -> str:
    src = html.escape(str(cfg["path"]), quote=True)
    return f"""
    <div class="image-box">
      <img src="{src}" alt="{src}" />
    </div>
  """


_group_counter = {"value": 0}


def _next_group_id() -> str:
    _group_counter["value"] += 1
    return f"grp-{_group_counter['value']}"


def _build_tabs_group(config: dict, base_dir: Path) -> str:
    group_id = _next_group_id()
    buttons = []
    panels = []

    for index, (name, node) in enumerate(config.items()):
        panel_id = f"{group_id}-panel-{index}"
        is_active = index == 0

        if _is_leaf_config(node):
            tab_type = str(node.get("type", "table")).lower().strip()
            if tab_type == "image":
                body = _build_image_panel(node)
            else:
                body = _build_table_panel(panel_id, node, base_dir)
        elif isinstance(node, dict):
            body = _build_tabs_group(node, base_dir)
        else:
            raise ValueError(f"Configuration invalide pour l'onglet '{name}'.")

        active_class = " active" if is_active else ""
        buttons.append(
            f'<button type="button" class="tab-btn{active_class}" data-target="{panel_id}">'
            f"{html.escape(str(name))}</button>"
        )
        panels.append(
            f'<div class="tab-panel{active_class}" id="{panel_id}">'
            f'<div class="panel-body">{body}</div></div>'
        )

    return f"""
    <div class="tabs-group" data-group="{group_id}">
      <div class="tabs-bar">{''.join(buttons)}</div>
      <div class="tabs-panels">{''.join(panels)}</div>
    </div>
  """


def export_table_html(config: dict, output_path: str, title: str = "Rapport") -> Path:
    """Génère un fichier HTML autonome (ouvrable directement dans un navigateur, sans serveur)
    à partir d'une configuration d'onglets (images et/ou tableaux Tabulator).

    :param config: dictionnaire de configuration des onglets (voir docstring du module).
    :param output_path: chemin du fichier HTML à générer.
    :param title: titre affiché en haut du rapport.
    :return: le chemin du fichier généré.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _group_counter["value"] = 0
    tabs_html = _build_tabs_group(config, output_path.parent.resolve())

    shared_js = SHARED_JS.replace(
        "__HIDDEN_URL_PREFIXES__",
        json.dumps(HIDDEN_URL_PREFIXES, ensure_ascii=False),
    )

    document = f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="{TABULATOR_CSS}" />
  <link rel="stylesheet" href="{FORKAWESOME_CSS}" crossorigin="anonymous"/>
  <style>{STYLE}</style>
</head>
<body>
  {tabs_html}

  <script src="{TABULATOR_JS}"></script>
  <script>
    {shared_js}
    (window.__tableQueue || []).forEach(function (init) {{ init(); }});
    wireTabGroups();
  </script>
</body>
</html>
"""

    output_path.write_text(document, encoding="utf-8")
    return output_path


if __name__ == "__main__":
    config_data = {
        "Annonces": {
            "Tournus": {
                "path": "annonces_tournus.csv",
                "groups": ["meuble", "type"],
                "highlight_error_columns": [],
                "default_filters": {
                    "loyer_total": "< 650",
                    "pieces": "> 1",
                },
                "default_columns_hidden": ['caracteristiques', 'description'],
                "columns_number": ["surface", "pieces", "loyer", "charges", "loyer_total", "depot", "frais_agence", "etage", "chambres", "salles_de_bain", "étages_total", "année"],
                "columns_select": ["ville", "ascenseur", "balcon", "chauffage_type", "chauffage_mod", "parking", "classe", "site", "meuble", "type"],
                "columns_nowrap": ["url", "titre"],
            },
            "Neuville": {
                "path": "annonces_neuville.csv",
                "groups": ["ville", "meuble", "type"],
                "highlight_error_columns": [],
                "default_filters": {
                    "pieces": "> 1",
                },
                "default_columns_hidden": ['caracteristiques', 'description'],
                "columns_number": ["surface", "pieces", "loyer", "charges", "loyer_total", "depot", "frais_agence", "etage", "chambres", "salles_de_bain", "étages_total", "année"],
                "columns_select": ["ville", "ascenseur", "balcon", "chauffage_type", "chauffage_mod", "parking", "classe", "site", "meuble", "type"],
                "columns_nowrap": ["url", "titre"],
            },
        },
    }

    report_title = _title_from_file_mtime("annonces_tournus.csv")
    result = export_table_html(config_data, "index.html", title=report_title)
    print(f"Rapport HTML généré : {result}")
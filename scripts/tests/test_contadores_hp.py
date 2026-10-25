"""Regresión de clasificación sin cargar/cambiar PaddleOCR.

Ejecuta las funciones de producción desde su AST para evitar los imports pesados
del motor. Sólo se sustituye la lectura OCR por texto controlado; se generan y
separan PDFs reales. Los dos árboles tienen entrypoints distintos en producción.
"""
import ast
from collections import Counter
import json
import os
from pathlib import Path
import re
import sys
import time
import types

import fitz
import pytest


ROOT = Path(__file__).resolve().parents[2]
FUNCTIONS = {
    'normalizar_sn', 'generar_variantes_ocr', 'validar_sn_contra_inventario',
    'modelo_hp_nuevo', 'datos_uso_hp', 'preparar_uso_hp', 'revisar_grupo_hp',
    'extraer_serial_etiqueta', 'extraer_serial_generico',
    'identificar_modelo', 'fase_logica', 'procesar_pdf', 'buscar_serial', 'texto_es_util',
}


def load_logic(folder, sources=None):
    ns = dict(re=re, os=os, fitz=fitz, time=time, sys=sys, json=json, Counter=Counter,
              WORKERS=1, DPI=200, ORDEN_SEDE_HUELLA=[], SEDE_HUELLA={},
              panel_ocr_jsonl=lambda: None, panel_job_evento=lambda *a, **k: None,
              panel_pagina=lambda *a, **k: None, log=lambda *a: None,
              reocr_pagina_rotada=lambda *a: '', reocr_serial_encabezado=lambda *a: None)
    for name in ('separador_pdf.py', 'separador_auto.py'):
        source = sources[name] if sources else (ROOT / folder / name).read_text(encoding='utf-8')
        tree = ast.parse(source)
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS
                 or isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ('patron_serial_ocr', 'PATRON_SERIAL_ETIQUETA') for t in n.targets)]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), name, 'exec'), ns)
    return ns


@pytest.fixture(params=['separador', 'separador_pdfs'])
def logic(request, monkeypatch):
    # La clasificación no necesita numpy ni Paddle; no simular su precisión OCR.
    monkeypatch.setitem(sys.modules, 'numpy', types.SimpleNamespace(sctypes={}))
    monkeypatch.setitem(sys.modules, 'paddleocr', types.SimpleNamespace(PaddleOCR=lambda **k: None))
    return load_logic(request.param)


def usage(model='X53052', page=1, sn='MX3CV6Q0Z'):
    return (f'Página de uso HP LaserJet MFP {model} Página {page} '
            + (f'Número de serie del producto: {sn} ' if sn else '')
            + ('Impresiones Imprimir Copiar Total general 40.2 Recuento de escaneados por destino' if page == 1 else 'Total general 9'))


def inventory(*serials):
    return {sn: dict(sn=sn, sede='SEDE', zona='1', zona_dir='ZONA_1', marca='HP',
                     modelo='', origen='inventario.xlsx') for sn in serials}


def run_auto(logic, tmp_path, texts, serials=('MX3CV6Q0Z',)):
    path = tmp_path / 'entrada.pdf'
    with fitz.open() as doc:
        for n in range(len(texts)):
            doc.new_page().insert_text((50, 50), f'Hoja origen {n + 1}')
        doc.save(path)
    logic['ocr_rango'] = lambda *args: [dict(num=n, texto=t) for n, t in enumerate(texts)]
    idx = inventory(*serials)
    return logic['procesar_pdf'](str(path), idx, {logic['normalizar_sn'](s): v for s, v in idx.items()})


@pytest.mark.parametrize('model', ['E40040', 'X53052'])
@pytest.mark.parametrize('two_pages', [False, True])
def test_uso_una_o_dos_hojas(logic, tmp_path, model, two_pages):
    texts = [f'Página configuración HP LaserJet {model} Número de serie del producto: MX3CV6Q0Z',
             usage(model), *([usage(model, 2, None)] if two_pages else []),
             f'HP Jetdirect incorporado HP LaserJet {model}']
    result = run_auto(logic, tmp_path, texts)
    assert result['ok'][0]['paginas'] == ([1, 2] if two_pages else [1])
    assert not result['verificar']
    assert [d['num'] for d in result['descartadas']] == [0, len(texts) - 1]
    assert result['ok'][0]['modelo'] == f'hp_{model.lower()}'


@pytest.mark.parametrize('texts,serials', [
    ([usage(page=2)], ('MX3CV6Q0Z',)),  # P2 con serie sigue siendo huérfana
    ([usage(), usage(page=1)], ('MX3CV6Q0Z',)),  # dos P1 no son un informe de dos hojas
    ([usage(), usage(page=2, sn='MX3CV6Q99')], ('MX3CV6Q0Z', 'MX3CV6Q99')),
    (['HP LaserJet X53052 Número de serie del producto: MX3CV6Q0Z contenido ilegible'], ('MX3CV6Q0Z',)),
])
def test_ambiguos_no_se_validan(logic, tmp_path, texts, serials):
    result = run_auto(logic, tmp_path, texts, serials)
    assert not result['ok']
    assert result['verificar']
    assert not result['descartadas']


@pytest.mark.parametrize('middle', ['', 'HP Jetdirect incorporado HP LaserJet X53052',
                                  'HP Laser MFP CNB1234567'])
def test_no_heredar_a_traves_de_otras_hojas(logic, tmp_path, middle):
    result = run_auto(logic, tmp_path, [usage(), middle, usage(page=2, sn=None)])
    assert result['ok'][0]['paginas'] == [0]
    assert any(2 in p['paginas'] for p in result['verificar'])


def test_no_mezclar_modelos(logic, tmp_path):
    result = run_auto(logic, tmp_path, [usage('E40040'), usage('X53052', 2, None)])
    assert result['ok'][0]['paginas'] == [0]
    assert result['verificar'][0]['paginas'] == [1]


def test_serie_nueva_fuera_de_inventario(logic, tmp_path):
    result = run_auto(logic, tmp_path, [usage(), usage(page=2, sn=None)], ())
    assert not result['ok']
    assert len(result['verificar']) == 1
    assert result['verificar'][0]['paginas'] == [0, 1]
    assert result['verificar'][0]['sn'] == 'MX3CV6Q0Z'


def test_serial_etiqueta_ocr_con_separadores(logic):
    f = logic['extraer_serial_etiqueta']
    assert f('Numero de_serie del producto:_VNG3H10339') == 'VNG3H10339'
    assert f('Numero de serie dei producto: MX3CV6QODK') == 'MX3CV6Q0DK'
    assert f('Product Serial Number: ABC123456') == 'ABC123456'
    assert f('Numero de serie del producto: 8158A4C7') == '8158A4C7'


def test_modelo_explicito_precede_prefijo_antiguo(logic):
    assert logic['identificar_modelo'](usage('E40040'), 'VNB1234567') == 'hp_e40040'
    assert logic['identificar_modelo']('HP LaserJet X53O5Z', 'CNB1234567') == 'hp_x53052'
    assert logic['identificar_modelo']('HP LaserJet M501', 'VNB1234567') == 'hp_501'
    assert logic['identificar_modelo']('HP Laser MFP 432', 'CNB1234567') == 'hp_mfp'


def test_separador_directo_genera_pdf_solo_uso(logic, tmp_path):
    texts = ['HP LaserJet X53052 Página configuración Número de serie del producto: MX3CV6Q0Z',
             usage(), usage(page=2, sn=None), 'HP Jetdirect HP LaserJet X53052']
    run_auto(logic, tmp_path, texts)
    ok, rev, groups = logic['fase_logica'](str(tmp_path / 'entrada.pdf'), dict(enumerate(texts)),
                                         str(tmp_path / 'salida'), {'MX3CV6Q0Z': 'MX3CV6Q0Z'})
    assert (ok, rev) == (1, 0)
    with fitz.open(groups[0]['archivo']) as result:
        assert len(result) == 2
        assert [p.get_text().strip() for p in result] == ['Hoja origen 2', 'Hoja origen 3']


def test_directo_p2_huerfana_no_hereda_serie_hp_antiguo(logic, tmp_path):
    texts = ['HP Laser MFP 432 CNB1234567', usage(page=2, sn=None)]
    run_auto(logic, tmp_path, texts)
    _, _, groups = logic['fase_logica'](str(tmp_path / 'entrada.pdf'), dict(enumerate(texts)),
                                       str(tmp_path / 'salida'), {'CNB1234567': 'CNB1234567'})
    assert len(groups) == 2
    assert all(g['paginas'] == 1 for g in groups)
    assert next(g for g in groups if g['modelo'] == 'hp_x53052')['revisar']


def test_layout_22_hojas_muestra(logic, tmp_path):
    # Transcripción mínima de las cabeceras visibles de Nuevas_2.pdf (no OCR real).
    texts = [usage('E40040', sn='VNG3H10262'), '',
             *['Página configuración HP LaserJet E40040'] * 6,
             usage('E40040', sn='VNG3H10339'), '',
             'Página configuración HP LaserJet MFP X53052', 'HP Jetdirect HP LaserJet MFP X53052',
             usage(), usage(page=2, sn=None),
             'Página configuración HP LaserJet MFP X53052', 'HP Jetdirect HP LaserJet MFP X53052',
             usage(sn='MX3CV60086'), '',
             'Página configuración HP LaserJet MFP X53052', 'HP Jetdirect HP LaserJet MFP X53052',
             usage(sn='MX3CV6Q0K'), usage(page=2, sn=None)]
    serials = ('VNG3H10262', 'VNG3H10339', 'MX3CV6Q0Z', 'MX3CV60086', 'MX3CV6Q0K')
    result = run_auto(logic, tmp_path, texts, serials)
    assert sorted(p['paginas'] for p in result['ok']) == [[0], [8], [12, 13], [16], [20, 21]]
    assert len(result['descartadas']) == 15
    assert not result['verificar']
    assert sum(len(p['paginas']) for p in result['ok'] + result['verificar']) + len(result['descartadas']) == 22


def test_helpers_identicos_en_ambos_motores():
    names = {'modelo_hp_nuevo', 'datos_uso_hp', 'preparar_uso_hp', 'revisar_grupo_hp'}
    def helpers(folder):
        tree = ast.parse((ROOT / folder / 'separador_pdf.py').read_text(encoding='utf-8'))
        return [ast.dump(n) for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert helpers('separador') == helpers('separador_pdfs')


LEGACY_CASES = [
    ('hp_501', ['HP LaserJet M501 VNB0B01234'], 'VNB0B01234', 1, 0),
    ('hp_501', ['HP LaserJet M501 VNB0B01234'] * 2, 'VNB0B01234', 0, 1),
    ('hp_mfp', ['HP Laser MFP 432 CNB1234567'] * 2, 'CNB1234567', 1, 0),
    ('pantum', ['Pantum BM5100 Page 1 3350PC12345', 'Pantum BM5100 Page 2'], '3350PC12345', 1, 0),
    ('pantum', ['Pantum BM5100 Page 1 3350PC12345'], '3350PC12345', 0, 1),
    ('ricoh', ['Ricoh Page counter:printing RICOH12345'] * 5, 'RICOH12345', 1, 0),
    ('ricoh', ['Ricoh Page counter:printing RICOH12345'] * 2, 'RICOH12345', 1, 0),
    ('kyocera', ['Kyocera Ecosys CR21234567'], 'CR21234567', 1, 0),
]


@pytest.mark.parametrize('model,texts,sn,ok,rev', LEGACY_CASES)
def test_modelos_anteriores(logic, tmp_path, model, texts, sn, ok, rev):
    result = run_auto(logic, tmp_path, texts, (sn,))
    assert (len(result['ok']), len(result['verificar'])) == (ok, rev)
    assert not result['descartadas']
    assert (result['ok'] or result['verificar'])[0]['modelo'] == model
    assert (result['ok'] or result['verificar'])[0]['paginas'] == list(range(len(texts)))


def test_lote_mixto_conserva_modelos_anteriores(logic, tmp_path):
    texts = ['HP LaserJet M501 VNB0B01234', usage(), usage(page=2, sn=None),
             'Pantum BM5100 Page 1 3350PC12345', 'Pantum BM5100 Page 2',
             'HP Jetdirect HP LaserJet X53052', 'Kyocera Ecosys CR21234567']
    result = run_auto(logic, tmp_path, texts, ('VNB0B01234', 'MX3CV6Q0Z', '3350PC12345', 'CR21234567'))
    assert sorted(p['paginas'] for p in result['ok']) == [[0], [1, 2], [3, 4], [6]]
    assert not result['verificar']
    assert [p['num'] for p in result['descartadas']] == [5]

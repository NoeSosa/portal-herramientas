#!/home/node/.openclaw/workspace/venv-pdfseparador/bin/python
"""
make_test_pdf.py — Genera un PDF de prueba (4 páginas) para el panel.

Se usa SOLO en pruebas: crea hojas sintéticas tipo "contador" con seriales
REALES del inventario para poder probar el flujo completo (OCR → clasificación →
sede → A_REVISAR/OK) sin necesidad de escanear nada.

Seriales usados (tomados de Inventarios_Separador/inventario.xlsx, hoja "Agosto 2026"):
  · 3356P351757  → Ricoh (2 páginas en el PDF = conteo válido)  → EQUIPOS_OK
  · CNB1NDR0XQ   → HP MFP 432 (1 página)                        → EQUIPOS_OK
  · (página 4 sin serial legible)                               → A_REVISAR

Uso:
  LD_LIBRARY_PATH="$HOME/.local/lib:$LD_LIBRARY_PATH" \
    venv-pdfseparador/bin/python separador_pdfs/panel/tests/make_test_pdf.py <salida.pdf>

✅ VALIDADO: 16/oct/2026 (Ches) — genera un PDF que el pipeline clasifica como
   2 grupos OK + 1 página A_REVISAR (verificado con --dry end-to-end).
"""
import os
import sys
import fitz  # PyMuPDF

RICOH_SN = "3356P351757"
HP_SN = "CNB1NDR0XQ"            # HP MFP 432 · sede OAJ CONSTITUCION NL
HP_SN_JERONIMO = "CNB1NDR0ZC"   # HP · sede 'Avenida San Jerónimo 999' (otra sede)

RICOH_PAG = [
    "RICOH IM 430F",
    "Contador de paginas / Page Counter",
    "Printing counter    : 128430",
    "Transmission counter: 2140",
    "Total counter       : 130570",
    f"Numero serie equipo : {RICOH_SN}",
]
HP_PAG = [
    "HP LaserJet MFP M432",
    "Contador de paginas",
    "Copier counter    : 4211",
    "Printer counter   : 17802",
    "Total pages       : 22013",
    f"Numero serie equipo : {HP_SN}",
]
SIN_ID = [
    "Contador de impresiones",
    "Reporte de uso mensual del equipo",
    "Copias realizadas : 812",
    "Impresiones       : 1975",
    "Total de clics    : 2787",
    "(hoja sin numero de serie legible)",
]
HP_JERONIMO = [
    "HP LaserJet MFP M432",
    "Contador de paginas",
    "Copier counter    : 9912",
    "Printer counter   : 41200",
    "Total pages       : 51112",
    f"Numero serie equipo : {HP_SN_JERONIMO}",
]

# Lotes que se generan juntos (sirven para probar el flujo completo sin escanear nada)
LOTES = {
    "lote_a_4p.pdf": [RICOH_PAG, RICOH_PAG, HP_PAG, SIN_ID],
    "lote_b_3p.pdf": [RICOH_PAG, RICOH_PAG, HP_PAG],
    "lote_c_2p.pdf": [HP_JERONIMO, SIN_ID],
}


def pagina(doc, lineas):
    p = doc.new_page(width=595, height=842)  # A4
    y = 90
    for i, t in enumerate(lineas):
        p.insert_text((70, y), t, fontname="helv", fontsize=22 if i == 0 else 18)
        y += 42
    return p


def main():
    destino = sys.argv[1] if len(sys.argv) > 1 else "."
    if destino.lower().endswith(".pdf"):
        # compatibilidad: un solo PDF de 4 páginas
        doc = fitz.open()
        for lineas in LOTES["lote_a_4p.pdf"]:
            pagina(doc, lineas)
        doc.save(destino)
        doc.close()
        print(f"OK -> {destino} (4 páginas)")
        return
    os.makedirs(destino, exist_ok=True)
    for nombre, hojas in LOTES.items():
        doc = fitz.open()
        for lineas in hojas:
            pagina(doc, lineas)
        ruta = os.path.join(destino, nombre)
        doc.save(ruta)
        doc.close()
        print(f"OK -> {ruta} ({len(hojas)} páginas)")


if __name__ == "__main__":
    main()

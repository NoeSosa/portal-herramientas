#!/usr/bin/env python3
"""
miniatura.py — Renderiza UNA página de un PDF a PNG (miniatura).
================================================================
Se ejecuta con el intérprete que tiene PyMuPDF (`PY_VENV_PDF`, es decir
`venv-pdfseparador/bin/python`), no con el python del panel.

Uso:
    python miniatura.py <ruta.pdf> <pagina 1-based> <ancho_px> <salida.png>

Imprime el total de páginas del PDF. Sirve para el visor "hoja en vivo" del panel
(ver la página que se está escaneando) y para las miniaturas por página.
"""
import sys


def main():
    if len(sys.argv) < 5:
        print("uso: miniatura.py <pdf> <pagina> <ancho_px> <salida.png>", file=sys.stderr)
        return 2
    import fitz  # PyMuPDF
    ruta, pagina, ancho, salida = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    doc = fitz.open(ruta)
    total = len(doc)
    i = max(0, min(pagina - 1, total - 1))
    pag = doc.load_page(i)
    # zoom para llegar al ancho pedido (acotado para no generar imágenes enormes)
    factor = max(0.15, min(3.0, ancho / max(1.0, pag.rect.width)))
    pix = pag.get_pixmap(matrix=fitz.Matrix(factor, factor), alpha=False)
    pix.save(salida)
    doc.close()
    print(total)
    return 0


if __name__ == "__main__":
    sys.exit(main())

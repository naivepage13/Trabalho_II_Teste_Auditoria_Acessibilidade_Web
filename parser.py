#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""parser.py - ponto de entrada do entregável. A implementação documentada está em parser_acessibilidade.py.

Uso:  python parser.py resultado_axe.json --corrigido resultado_axe_corrigido.json
"""
import sys

from parser_acessibilidade import main

if __name__ == "__main__":
    sys.exit(main())

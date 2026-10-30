#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
parser_acessibilidade.py - Parser do relatório JSON do axe-core (Portal TechInclusiva)

Fluxo implementado (roteiro da atividade, seção 4):
    1. Leitura de dados ........ carrega resultado_axe.json (gerado pelo axe CLI)
    2. Extração de falhas ...... isola as regras reprovadas (violations) e as que
                                 exigem revisão manual (incomplete)
    3. Categorização ........... agrupa por severidade: critical > serious > moderate > minor
    4. Mapeamento .............. lista seletores CSS / trechos HTML de cada regra e
                                 converte as tags do axe em critérios de sucesso da WCAG 2.1
    5. Relatório final ......... resumo consolidado no terminal + relatório em PDF

Uso:
    python parser_acessibilidade.py resultado_axe.json
    python parser_acessibilidade.py resultado_axe.json --corrigido resultado_axe_corrigido.json
    python parser_acessibilidade.py resultado_axe.json --falhar-se serious     # uso em pipeline/CI

Dependências: Python 3.9+ e reportlab (pip install -r requirements.txt).
Formato aceito: saída do axe-core / @axe-core/cli (lista com 1 resultado por página ou objeto).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# Tabelas de referência
# --------------------------------------------------------------------------- #

SEVERIDADES = ["critical", "serious", "moderate", "minor"]          # da mais para a menos grave
ROTULO = {"critical": "Crítica", "serious": "Séria", "moderate": "Moderada", "minor": "Leve"}
PESO = {"critical": 4, "serious": 3, "moderate": 2, "minor": 1}
COR_PDF = {"critical": "#b71c1c", "serious": "#e65100", "moderate": "#b28704", "minor": "#1565c0"}
COR_ANSI = {"critical": "\033[1;31m", "serious": "\033[31m", "moderate": "\033[33m", "minor": "\033[36m"}

# Critérios de sucesso WCAG 2.1 níveis A e AA: número -> (nome em português, nível)
WCAG_21 = {
    "1.1.1": ("Conteúdo não textual", "A"),
    "1.2.1": ("Somente áudio e somente vídeo (pré-gravado)", "A"),
    "1.2.2": ("Legendas (pré-gravado)", "A"),
    "1.2.3": ("Audiodescrição ou mídia alternativa (pré-gravado)", "A"),
    "1.2.4": ("Legendas (ao vivo)", "AA"),
    "1.2.5": ("Audiodescrição (pré-gravado)", "AA"),
    "1.3.1": ("Informações e relações", "A"),
    "1.3.2": ("Sequência significativa", "A"),
    "1.3.3": ("Características sensoriais", "A"),
    "1.3.4": ("Orientação", "AA"),
    "1.3.5": ("Identificar a finalidade da entrada", "AA"),
    "1.4.1": ("Uso de cor", "A"),
    "1.4.2": ("Controle de áudio", "A"),
    "1.4.3": ("Contraste (mínimo)", "AA"),
    "1.4.4": ("Redimensionar texto", "AA"),
    "1.4.5": ("Imagens de texto", "AA"),
    "1.4.10": ("Reflow", "AA"),
    "1.4.11": ("Contraste não textual", "AA"),
    "1.4.12": ("Espaçamento de texto", "AA"),
    "1.4.13": ("Conteúdo em foco ou hover", "AA"),
    "2.1.1": ("Teclado", "A"),
    "2.1.2": ("Sem armadilha de teclado", "A"),
    "2.1.4": ("Atalhos de teclas de caracteres", "A"),
    "2.2.1": ("Tempo ajustável", "A"),
    "2.2.2": ("Pausar, parar, ocultar", "A"),
    "2.3.1": ("Três flashes ou abaixo do limite", "A"),
    "2.4.1": ("Ignorar blocos", "A"),
    "2.4.2": ("Página com título", "A"),
    "2.4.3": ("Ordem do foco", "A"),
    "2.4.4": ("Finalidade do link (em contexto)", "A"),
    "2.4.5": ("Várias formas", "AA"),
    "2.4.6": ("Cabeçalhos e rótulos", "AA"),
    "2.4.7": ("Foco visível", "AA"),
    "2.5.1": ("Gestos de ponteiro", "A"),
    "2.5.2": ("Cancelamento de ponteiro", "A"),
    "2.5.3": ("Rótulo no nome", "A"),
    "2.5.4": ("Atuação por movimento", "A"),
    "3.1.1": ("Idioma da página", "A"),
    "3.1.2": ("Idioma das partes", "AA"),
    "3.2.1": ("Em foco", "A"),
    "3.2.2": ("Em entrada de dados", "A"),
    "3.2.3": ("Navegação consistente", "AA"),
    "3.2.4": ("Identificação consistente", "AA"),
    "3.3.1": ("Identificação do erro", "A"),
    "3.3.2": ("Rótulos ou instruções", "A"),
    "3.3.3": ("Sugestão de erro", "AA"),
    "3.3.4": ("Prevenção de erros (jurídicos, financeiros, dados)", "AA"),
    "4.1.1": ("Análise sintática", "A"),
    "4.1.2": ("Nome, função, valor", "A"),
    "4.1.3": ("Mensagens de status", "AA"),
}

# Regras "best-practice" do axe não equivalem a um critério WCAG; relacionamos o mais próximo.
CRITERIOS_RELACIONADOS = {
    "tabindex": ["2.4.3"],
    "landmark-one-main": ["1.3.1", "2.4.1"],
    "page-has-heading-one": ["1.3.1", "2.4.6"],
    "region": ["1.3.1", "2.4.1"],
    "empty-heading": ["2.4.6"],
    "empty-table-header": ["1.3.1"],
    "heading-order": ["1.3.1"],
}

# Explicações e correções em português (o axe devolve apenas texto em inglês)
REMEDIACAO = {
    "aria-required-attr": (
        "Atributos ARIA obrigatórios ausentes",
        "Um role ARIA exige atributos próprios (ex.: aria-checked em role=\"checkbox\"). Sem eles, o leitor de tela não informa o estado do controle.",
        "Preferir o elemento nativo (<input type=\"checkbox\">) ou incluir o atributo exigido e mantê-lo atualizado via script."),
    "button-name": (
        "Botão sem nome acessível",
        "Usuários de leitor de tela ouvem apenas \"botão\" e não sabem qual é a ação.",
        "Incluir texto visível, aria-label ou aria-labelledby. Ícones SVG decorativos devem ter aria-hidden=\"true\"."),
    "image-alt": (
        "Imagem sem texto alternativo",
        "Sem alt a imagem é lida pelo nome do arquivo ou ignorada, e a informação fica indisponível para pessoas cegas.",
        "Incluir alt descritivo. Imagens puramente decorativas recebem alt=\"\". Imagem dentro de link descreve o destino do link."),
    "label": (
        "Campo de formulário sem rótulo",
        "O placeholder desaparece ao digitar e não substitui um rótulo; o leitor de tela não sabe o que o campo pede.",
        "Associar <label for=\"id\"> ao campo (ou usar aria-label quando o rótulo visual não for possível)."),
    "meta-refresh": (
        "Atualização automática da página",
        "Recarregar a página sem aviso faz o usuário perder a posição de leitura e o foco; viola o controle de tempo (WCAG 2.2.1).",
        "Remover <meta http-equiv=\"refresh\"> e, se necessário, oferecer um botão para o usuário atualizar quando quiser."),
    "select-name": (
        "Lista suspensa sem rótulo",
        "O leitor de tela anuncia apenas \"caixa de combinação\", sem dizer o que deve ser escolhido.",
        "Associar um <label for=\"id\"> ao <select> (ou aria-label)."),
    "aria-hidden-focus": (
        "Conteúdo focável dentro de área aria-hidden",
        "O teclado foca um elemento que o leitor de tela não enxerga (foco \"fantasma\").",
        "Remover aria-hidden do contêiner ou tornar os filhos não focáveis (tabindex=\"-1\" ou removê-los do DOM)."),
    "autocomplete-valid": (
        "Valor de autocomplete inválido",
        "Navegadores e tecnologias assistivas não conseguem identificar a finalidade do campo nem preenchê-lo (WCAG 1.3.5).",
        "Usar apenas os tokens padronizados do HTML, como name, email, tel, street-address."),
    "color-contrast": (
        "Contraste de cores insuficiente",
        "Texto com baixo contraste é ilegível para pessoas com baixa visão ou daltonismo, ou sob luz forte.",
        "Garantir razão mínima de 4,5:1 (texto normal) ou 3:1 (texto grande); ajustar a cor do texto ou do fundo."),
    "document-title": (
        "Página sem título",
        "O <title> é a primeira informação lida pelo leitor de tela e identifica a aba e o histórico do navegador.",
        "Incluir um <title> descritivo e único em <head>."),
    "frame-title": (
        "iframe sem título",
        "Sem title, o leitor de tela anuncia apenas \"quadro\" e o usuário não sabe se vale a pena entrar nele.",
        "Incluir o atributo title descrevendo o conteúdo do iframe."),
    "html-has-lang": (
        "Idioma da página não declarado",
        "Leitores de tela escolhem a pronúncia pelo idioma; sem lang, texto em português pode ser lido com a voz errada.",
        "Declarar o idioma na raiz: <html lang=\"pt-BR\">."),
    "link-name": (
        "Link sem texto discernível",
        "Links vazios são anunciados apenas como \"link\" e não indicam o destino (WCAG 2.4.4 e 4.1.2).",
        "Incluir texto no link, alt na imagem interna ou aria-label."),
    "list": (
        "Estrutura de lista inválida",
        "Leitores de tela contam e anunciam itens de lista; filhos que não são <li> quebram essa semântica.",
        "<ul> e <ol> devem conter somente <li> (além de <script> e <template>)."),
    "tabindex": (
        "tabindex maior que zero",
        "Valores positivos alteram a ordem natural de tabulação e deixam a navegação por teclado confusa (WCAG 2.4.3).",
        "Usar tabindex=\"0\" ou \"-1\" e organizar o DOM na ordem lógica de leitura."),
    "landmark-one-main": (
        "Página sem landmark principal (<main>)",
        "Usuários de leitor de tela saltam direto ao conteúdo principal por landmarks; sem <main> precisam percorrer tudo.",
        "Envolver o conteúdo principal em um único <main>."),
    "meta-viewport": (
        "Zoom bloqueado pelo viewport",
        "user-scalable=no ou maximum-scale baixo impede ampliar a página (WCAG 1.4.4), barrando pessoas com baixa visão.",
        "Remover user-scalable=no e maximum-scale: <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">."),
    "page-has-heading-one": (
        "Página sem título de nível 1 (<h1>)",
        "O <h1> identifica o assunto da página e serve de ponto de partida para a navegação por títulos.",
        "Incluir um <h1> com o título principal da página."),
    "region": (
        "Conteúdo fora de landmarks",
        "Usuários de leitor de tela navegam por regiões (cabeçalho, navegação, principal, rodapé); conteúdo solto é difícil de achar.",
        "Usar <header>, <nav>, <main>, <aside> e <footer> para conter todo o conteúdo visível."),
    "empty-heading": (
        "Título vazio",
        "Um título sem texto é anunciado como \"título\" sem conteúdo e polui a navegação por títulos.",
        "Preencher o título ou removê-lo."),
    "empty-table-header": (
        "Cabeçalho de tabela vazio",
        "A célula <th> vazia não identifica a coluna ou a linha para o leitor de tela.",
        "Preencher o <th> com o nome da coluna/linha ou usar <td> se não for cabeçalho."),
    "video-caption": (
        "Vídeo sem legendas",
        "Pessoas surdas ou com deficiência auditiva não têm acesso ao áudio do vídeo (WCAG 1.2.2).",
        "Incluir <track kind=\"captions\" src=\"...vtt\" srclang=\"pt-BR\"> e oferecer transcrição."),
    "link-in-text-block": (
        "Link distinguido apenas pela cor",
        "Quem não distingue cores não identifica os links no meio do texto (WCAG 1.4.1).",
        "Manter o sublinhado (ou outra pista visual) e contraste mínimo de 3:1 com o texto ao redor."),
    "heading-order": (
        "Ordem dos títulos fora de sequência",
        "Saltar níveis (h2 para h4) confunde quem navega pela estrutura de títulos.",
        "Usar os níveis em sequência, sem saltos."),
    "nested-interactive": (
        "Controles interativos aninhados",
        "Um controle dentro de outro (ex.: botão dentro de link) não é anunciado nem acionado de forma confiável.",
        "Usar apenas um elemento interativo (link estilizado como botão, ou botão com ação)."),
    "bypass": (
        "Sem mecanismo para pular blocos repetidos",
        "Usuários de teclado precisam tabular por todo o menu a cada página (WCAG 2.4.1).",
        "Incluir um link \"Pular para o conteúdo\" no início e/ou landmarks."),
    "valid-lang": (
        "Código de idioma inválido",
        "Um lang inválido faz o leitor de tela ignorar a troca de idioma.",
        "Usar códigos BCP 47 válidos (pt-BR, en, es...)."),
}

# Observações sobre itens "needs review" (o axe não decide sozinho)
NOTAS_REVISAR = {
    "video-caption": "O axe sempre marca <video> como revisão manual, pois não valida o conteúdo das legendas. "
                     "Por inspeção do código, o <video> original não tem nenhum <track kind=\"captions\">: tratar como falha.",
}

# Verificações que a automação não cobre (a própria Deque estima 20-57% de cobertura).
# Os textos abaixo são específicos do index.html do case TechInclusiva.
VERIFICACOES_MANUAIS = [
    ("Foco visível (WCAG 2.4.7)",
     "O CSS original tem *:focus { outline: none }, removendo o indicador de foco. O axe não mede isso; "
     "confirmar navegando com Tab que o foco aparece em todos os controles."),
    ("Rótulo só com placeholder (WCAG 1.3.1 e 3.3.2)",
     "Os campos \"Buscar notícias\", \"Seu nome\" e \"Seu e-mail\" usam apenas placeholder. O axe aceita o placeholder "
     "como nome acessível e não reprova, mas ele some ao digitar. A correção adotada usa <label> explícito."),
    ("Controle personalizado por teclado (WCAG 2.1.1)",
     "O \"Modo escuro\" é um <div role=\"checkbox\">: além do aria-checked, seria preciso tratar Espaço/Enter. "
     "A correção adotada troca por <input type=\"checkbox\"> nativo."),
    ("Elementos interativos aninhados",
     "O link com <button> dentro (\"Ler matéria completa\") é HTML inválido e pode não ser acionado por leitores de tela; "
     "o axe não reprovou nesta página."),
    ("Hierarquia de títulos (WCAG 1.3.1)",
     "A página usa apenas <h4> (e um <h3> vazio) e nenhum <h1>/<h2>. O axe só reportou a ausência do <h1> e o título vazio."),
    ("Uso de cor (WCAG 1.4.1)",
     "O link \"WCAG 2.1\" dentro do parágrafo é distinguido só pela cor (sem sublinhado). O axe não o reprovou; "
     "conferir visualmente. A correção adotada mantém o sublinhado."),
    ("Qualidade dos textos alternativos e das legendas",
     "O axe verifica a presença do alt/track, não se o conteúdo é útil. Revisar com leitor de tela (NVDA ou Narrador)."),
    ("Teste com tecnologia assistiva real",
     "Percorrer a página com NVDA/Narrador, só com o teclado, com zoom de 200% e com contraste alto do Windows."),
]


# --------------------------------------------------------------------------- #
# Modelo de dados
# --------------------------------------------------------------------------- #

@dataclass
class Elemento:
    """Um nó do DOM afetado por uma regra."""
    seletor: str
    html: str
    detalhe: str = ""


@dataclass
class Regra:
    """Uma regra do axe (reprovada ou a revisar) com os elementos afetados."""
    id: str
    impacto: str
    descricao: str
    ajuda_url: str
    tags: list
    criterios: list = field(default_factory=list)       # [(numero, nome, nivel)]
    relacionados: list = field(default_factory=list)    # critérios próximos (regras best-practice)
    elementos: list = field(default_factory=list)

    @property
    def nivel(self) -> str:
        """'A', 'AA' ou 'Boa prática' (o menor nível entre os critérios violados)."""
        niveis = {n for _, _, n in self.criterios}
        if "A" in niveis:
            return "A"
        if "AA" in niveis:
            return "AA"
        return "Boa prática"

    @property
    def nivel_txt(self) -> str:
        """Nível para exibição: "nível A", "nível AA" ou "boa prática"."""
        return "boa prática" if self.nivel == "Boa prática" else f"nível {self.nivel}"

    @property
    def titulo(self) -> str:
        """Título da regra em português (usa a descrição do axe se não houver tradução)."""
        return REMEDIACAO.get(self.id, (self.descricao,))[0]

    @property
    def por_que(self) -> str:
        """Impacto da falha para o usuário, em português."""
        return REMEDIACAO[self.id][1] if self.id in REMEDIACAO else self.descricao

    @property
    def correcao(self) -> str:
        """Orientação de correção, em português."""
        return REMEDIACAO[self.id][2] if self.id in REMEDIACAO else f"Consulte a documentação: {self.ajuda_url}"

    @property
    def criterios_txt(self) -> str:
        """Critérios WCAG para exibição; regras de boa prática mostram o critério relacionado."""
        if self.criterios:
            return ", ".join(c for c, _, _ in self.criterios)
        if self.relacionados:
            return "relac.: " + ", ".join(self.relacionados)
        return "-"


@dataclass
class Relatorio:
    """Resultado consolidado de uma auditoria axe: metadados, violações priorizadas e itens a revisar."""
    url: str
    data: datetime
    motor: str
    tags: list
    ambiente: str
    violacoes: list
    revisar: list
    passes: int
    inaplicaveis: int

    @property
    def total_elementos(self) -> int:
        """Soma dos elementos afetados em todas as violações."""
        return sum(len(r.elementos) for r in self.violacoes)

    def por_severidade(self) -> dict:
        """severidade -> (nº de regras, nº de elementos)."""
        return {s: (sum(1 for r in self.violacoes if r.impacto == s),
                    sum(len(r.elementos) for r in self.violacoes if r.impacto == s))
                for s in SEVERIDADES}

    def por_nivel(self) -> Counter:
        """Conta as regras violadas por nível (A, AA ou Boa prática)."""
        return Counter(r.nivel for r in self.violacoes)


# --------------------------------------------------------------------------- #
# 1. Leitura  |  2. Extração  |  3. Categorização  |  4. Mapeamento
# --------------------------------------------------------------------------- #

def carregar_json(caminho: str) -> dict:
    """Lê o arquivo do axe. O CLI grava uma lista (1 item por página); aceita também um objeto."""
    arquivo = Path(caminho)
    if not arquivo.is_file():
        raise SystemExit(f"Arquivo não encontrado: {arquivo}")
    bruto = json.loads(arquivo.read_text(encoding="utf-8-sig"))
    if isinstance(bruto, list):
        if not bruto:
            raise SystemExit(f"O arquivo {arquivo} está vazio.")
        bruto = bruto[0]
    if "audits" in bruto and "categories" in bruto:
        raise SystemExit("Formato Lighthouse detectado: esta versão do parser lê apenas a saída do axe-core.")
    if "violations" not in bruto:
        raise SystemExit(f"{arquivo} não parece ser uma saída do axe-core (campo 'violations' ausente).")
    return bruto


def decodificar_tags(tags: list) -> list:
    """Converte tags do axe ('wcag143', 'wcag1410') em critérios [(numero, nome, nivel)]."""
    criterios = []
    for tag in tags:
        m = re.fullmatch(r"wcag(\d)(\d)(\d{1,2})", tag)
        if m:
            numero = ".".join(m.groups())
            nome, nivel = WCAG_21.get(numero, ("Critério fora da WCAG 2.1 A/AA", "?"))
            criterios.append((numero, nome, nivel))
    return sorted(criterios, key=lambda c: [int(p) for p in c[0].split(".")])


def _seletor(target: list) -> str:
    """O 'target' do axe pode ser aninhado (iframes e shadow DOM)."""
    return " >> ".join(t if isinstance(t, str) else " ".join(t) for t in target)


def _detalhe(regra_id: str, no: dict) -> str:
    """Informação extra por elemento; hoje só o contraste (razão medida x exigida)."""
    if regra_id != "color-contrast":
        return ""
    for grupo in ("any", "all", "none"):
        for chk in no.get(grupo, []):
            d = chk.get("data") or {}
            if "contrastRatio" in d:
                razao = f"{d['contrastRatio']}".replace(".", ",")
                minimo = f"{d.get('expectedContrastRatio', '')}".replace(".", ",")
                return f"contraste {razao}:1 (mínimo {minimo}); texto {d.get('fgColor')} sobre {d.get('bgColor')}"
    return ""


def _montar_regra(item: dict) -> Regra:
    """Converte um item de "violations"/"incomplete" do axe em uma Regra com seus elementos."""
    tags = item.get("tags", [])
    criterios = decodificar_tags(tags)
    impacto = item.get("impact") or next((n.get("impact") for n in item.get("nodes", []) if n.get("impact")), "minor")
    regra = Regra(
        id=item["id"], impacto=impacto, descricao=item.get("help", item["id"]),
        ajuda_url=item.get("helpUrl", ""), tags=tags, criterios=criterios,
        relacionados=[] if criterios else CRITERIOS_RELACIONADOS.get(item["id"], []),
    )
    for no in item.get("nodes", []):
        regra.elementos.append(Elemento(
            seletor=_seletor(no.get("target", [])),
            html=" ".join(no.get("html", "").split()),
            detalhe=_detalhe(item["id"], no),
        ))
    return regra


def _priorizar(regras: list) -> list:
    """Severidade primeiro, depois quantidade de elementos afetados."""
    return sorted(regras, key=lambda r: (-PESO.get(r.impacto, 0), -len(r.elementos), r.id))


def extrair(bruto: dict) -> Relatorio:
    """Etapas 2 e 3: isola violações e itens a revisar do JSON e ordena por prioridade."""
    amb = bruto.get("testEnvironment", {})
    motor = bruto.get("testEngine", {})
    ts = bruto.get("timestamp")
    try:
        data = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except (AttributeError, ValueError):
        data = datetime.now().astimezone()
    return Relatorio(
        url=bruto.get("url", "(desconhecida)"),
        data=data,
        motor=f"{motor.get('name', 'axe-core')} {motor.get('version', '')}".strip(),
        tags=bruto.get("toolOptions", {}).get("runOnly", {}).get("values", []),
        ambiente=f"{amb.get('userAgent', '')} | janela {amb.get('windowWidth', '?')}x{amb.get('windowHeight', '?')}",
        violacoes=_priorizar([_montar_regra(v) for v in bruto.get("violations", [])]),
        revisar=_priorizar([_montar_regra(v) for v in bruto.get("incomplete", [])]),
        passes=len(bruto.get("passes", [])),
        inaplicaveis=len(bruto.get("inapplicable", [])),
    )


def agrupar_por_criterio(regras: list) -> dict:
    """Mapeia cada critério WCAG para as regras que o violam."""
    mapa: dict = {}
    for r in regras:
        for numero, _, _ in r.criterios:
            mapa.setdefault(numero, []).append(r)
    return dict(sorted(mapa.items(), key=lambda kv: [int(p) for p in kv[0].split(".")]))


def comparar(antes: Relatorio, depois: Relatorio) -> list:
    """Linhas (severidade, regras antes, elementos antes, regras depois, elementos depois)."""
    a, d = antes.por_severidade(), depois.por_severidade()
    return [(s, *a[s], *d[s]) for s in SEVERIDADES]


# --------------------------------------------------------------------------- #
# 5a. Resumo no terminal
# --------------------------------------------------------------------------- #

def imprimir_resumo(rel: Relatorio, depois: Relatorio | None, cor: bool, mostrar_html: bool) -> None:
    """Etapa 5a: imprime no terminal o resumo, as violações priorizadas e o comparativo antes x depois."""
    def c(codigo: str, texto: str) -> str:
        """Aplica cor ANSI quando habilitada."""
        return f"{codigo}{texto}\033[0m" if cor else texto

    linha = "=" * 78
    print(linha)
    print(c("\033[1m", " RELATÓRIO DE ACESSIBILIDADE - Portal TechInclusiva (WCAG 2.1 A/AA)"))
    print(linha)
    print(f" Página auditada : {rel.url}")
    print(f" Data da análise : {rel.data:%d/%m/%Y %H:%M}")
    print(f" Motor           : {rel.motor}  (tags: {', '.join(rel.tags)})")
    print()
    print(f" Regras violadas : {len(rel.violacoes)}    Elementos afetados: {rel.total_elementos}")
    print(f" Regras aprovadas: {rel.passes}    A revisar (manual): {len(rel.revisar)}    Não aplicáveis: {rel.inaplicaveis}")
    print()

    print(" Por severidade")
    maior = max([e for _, e in rel.por_severidade().values()] + [1])
    for sev, (n_regras, n_elem) in rel.por_severidade().items():
        barra = "█" * max(1 if n_elem else 0, round(30 * n_elem / maior))
        print(f"   {c(COR_ANSI[sev], f'{ROTULO[sev]:<9}')} {n_regras:>2} regras | {n_elem:>2} elementos  {c(COR_ANSI[sev], barra)}")
    niveis = rel.por_nivel()
    print(f"\n Por nível WCAG   : A = {niveis['A']} | AA = {niveis['AA']} | boa prática (sem critério direto) = {niveis['Boa prática']}")
    print()

    print(linha)
    print(" VIOLAÇÕES PRIORIZADAS (severidade, depois quantidade de elementos)")
    print(linha)
    for i, r in enumerate(rel.violacoes, 1):
        etiqueta = c(COR_ANSI[r.impacto], f"[{ROTULO[r.impacto].upper()}]")
        print(f"\n{i:>2}. {etiqueta} {r.id}  -  {r.titulo}")
        print(f"    WCAG: {r.criterios_txt} ({r.nivel_txt}) | {len(r.elementos)} elemento(s)")
        for el in r.elementos:
            extra = f"  <- {el.detalhe}" if el.detalhe else ""
            print(f"      - {el.seletor}{extra}")
            if mostrar_html:
                print(f"          {el.html[:110]}{'...' if len(el.html) > 110 else ''}")
        print(f"    Correção: {textwrap.shorten(r.correcao, 200)}")

    if rel.revisar:
        print("\n" + linha)
        print(" A REVISAR MANUALMENTE (axe 'incomplete')")
        print(linha)
        for r in rel.revisar:
            print(f"  - {r.id}: {r.titulo} ({len(r.elementos)} elemento(s)) -> " + ", ".join(e.seletor for e in r.elementos))

    if depois is not None:
        print("\n" + linha)
        print(" ANTES x DEPOIS DA CORREÇÃO (regras / elementos)")
        print(linha)
        for sev, ra, ea, rd, ed in comparar(rel, depois):
            print(f"   {ROTULO[sev]:<9} {ra:>2} / {ea:>2}   ->   {rd:>2} / {ed:>2}")
        print(f"   {'TOTAL':<9} {len(rel.violacoes):>2} / {rel.total_elementos:>2}   ->   {len(depois.violacoes):>2} / {depois.total_elementos:>2}")
    print()


# --------------------------------------------------------------------------- #
# 5b. Relatório em PDF (reportlab)
# --------------------------------------------------------------------------- #

def _limpar(txt: str) -> str:
    """Fontes padrão do PDF usam cp1252: troca caracteres fora dessa faixa por '?'."""
    return txt.encode("cp1252", "replace").decode("cp1252")


def _quebrar_codigo(texto: str, largura: int = 104) -> str:
    """Quebra linhas longas de código preservando a indentação e numera as linhas."""
    saida = []
    for n, linha in enumerate(texto.splitlines(), 1):
        recuo = len(linha) - len(linha.lstrip())
        partes = textwrap.wrap(linha, width=largura, subsequent_indent=" " * (recuo + 4),
                               break_long_words=True, break_on_hyphens=False, replace_whitespace=False) or [""]
        for k, parte in enumerate(partes):
            saida.append(f"{n:>3}  {parte}" if k == 0 else f"     {parte}")
    return "\n".join(saida)


def gerar_pdf(rel: Relatorio, depois: Relatorio | None, destino: Path, html_corrigido: Path | None,
              github: str, integrantes: list, capturas: tuple) -> None:
    """Etapa 5b: gera o relatório técnico em PDF (capa, resumo, diagnóstico por regra e anexos)."""
    from xml.sax.saxutils import escape as esc

    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (HRFlowable, Image, PageBreak, Paragraph, Preformatted,
                                    SimpleDocTemplate, Spacer, Table, TableStyle)

    AZUL, CINZA = colors.HexColor("#0b3d91"), colors.HexColor("#f5f7fb")
    base = getSampleStyleSheet()
    st = {
        "corpo": ParagraphStyle("corpo", parent=base["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13),
        "peq": ParagraphStyle("peq", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=10.5),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=15, textColor=AZUL,
                             spaceBefore=14, spaceAfter=6, keepWithNext=1),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11.5, textColor=AZUL,
                             spaceBefore=8, spaceAfter=4, keepWithNext=1),
        "cod": ParagraphStyle("cod", parent=base["Code"], fontName="Courier", fontSize=7, leading=8.6, wordWrap="CJK", leftIndent=0),
        "celula": ParagraphStyle("celula", parent=base["BodyText"], fontName="Helvetica", fontSize=8, leading=10),
        "capa_t": ParagraphStyle("capa_t", fontName="Helvetica-Bold", fontSize=24, leading=29, textColor=AZUL),
        "capa_s": ParagraphStyle("capa_s", fontName="Helvetica", fontSize=13, leading=18, textColor=colors.HexColor("#333333")),
        "centro": ParagraphStyle("centro", parent=base["BodyText"], alignment=TA_CENTER, fontSize=8, textColor=colors.grey),
    }

    def P(txt, estilo="corpo"):
        """Parágrafo com o texto saneado para a codificação das fontes padrão do PDF."""
        return Paragraph(_limpar(txt), st[estilo])

    def tabela(dados, larguras, cabecalho=True, extra=None):
        """Tabela com o estilo padrão do relatório (cabeçalho azul e linhas alternadas)."""
        t = Table(dados, colWidths=larguras, repeatRows=1 if cabecalho else 0)
        estilo = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#c7cdd9")),
                  ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                  ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if cabecalho:
            estilo += [("BACKGROUND", (0, 0), (-1, 0), AZUL), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                       ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, CINZA])]
        t.setStyle(TableStyle(estilo + (extra or [])))
        return t

    def cab(*textos):
        """Linha de cabeçalho de tabela: texto branco em negrito."""
        return [Paragraph(f'<font color="white"><b>{esc(t)}</b></font>', st["celula"]) for t in textos]

    def grafico() -> Drawing:
        """Gráfico de barras com os elementos afetados por severidade."""
        d = Drawing(480, 100)
        dados = rel.por_severidade()
        maior = max([e for _, e in dados.values()] + [1])
        for i, sev in enumerate(SEVERIDADES):
            y = 78 - i * 24
            n_regras, n_elem = dados[sev]
            d.add(String(0, y + 4, ROTULO[sev], fontName="Helvetica-Bold", fontSize=9))
            largura = 280 * n_elem / maior
            d.add(Rect(70, y, max(largura, 1.5), 15, fillColor=colors.HexColor(COR_PDF[sev]), strokeColor=None))
            d.add(String(76 + largura, y + 4, f"{n_elem} elementos em {n_regras} regras", fontName="Helvetica", fontSize=8.5))
        return d

    def capa(canvas, doc):
        """Decoração da primeira página (faixa azul no topo)."""
        canvas.saveState()
        canvas.setFillColor(AZUL)
        canvas.rect(0, A4[1] - 1.2 * cm, A4[0], 1.2 * cm, stroke=0, fill=1)
        canvas.restoreState()

    def rodape(canvas, doc):
        """Rodapé com o título do relatório e o número da página."""
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(2 * cm, 1.2 * cm, "Portal TechInclusiva - Auditoria de Acessibilidade (WCAG 2.1 A/AA)")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Página {doc.page}")
        canvas.restoreState()

    destino.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(destino), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=2 * cm,
                            bottomMargin=2 * cm, title="Relatório de Auditoria de Acessibilidade - Portal TechInclusiva",
                            author="; ".join(integrantes))
    L = 17 * cm
    s = []

    # ---- Capa -------------------------------------------------------------
    s += [Spacer(1, 3.5 * cm), P("Trabalho Prático II", "capa_s"), Spacer(1, 6),
          P("Teste de Auditoria de Acessibilidade Web", "capa_t"), Spacer(1, 6),
          P("Case real: Portal TechInclusiva - conformidade com a WCAG 2.1 (níveis A e AA)", "capa_s"),
          Spacer(1, 1.2 * cm), HRFlowable(width="100%", thickness=1.5, color=AZUL), Spacer(1, 0.6 * cm)]
    s.append(tabela([
        [P("<b>Unidade Curricular</b>"), P("Qualidade e Teste de Software")],
        [P("<b>Professor</b>"), P("Prof. MSc. Hugo Menezes Barra")],
        [P("<b>Integrantes</b>"), P("<br/>".join(esc(i) for i in integrantes))],
        [P("<b>Ferramentas</b>"), P(f"VS Code, {esc(rel.motor)} (CLI), Python, reportlab")],
        [P("<b>Página auditada</b>"), P(esc(rel.url))],
        [P("<b>Data da auditoria</b>"), P(f"{rel.data:%d/%m/%Y}")],
        [P("<b>Repositório GitHub</b>"), P(esc(github))],
    ], [4.2 * cm, L - 4.2 * cm], cabecalho=False, extra=[("BACKGROUND", (0, 0), (0, -1), CINZA)]))
    s.append(PageBreak())

    # ---- 1. Resumo executivo ----------------------------------------------
    sev = rel.por_severidade()
    s.append(P("1. Resumo executivo", "h1"))
    s.append(P(
        f"A varredura automatizada com <b>{esc(rel.motor)}</b> encontrou <b>{len(rel.violacoes)} regras violadas</b>, "
        f"afetando <b>{rel.total_elementos} elementos</b> da página inicial do portal: "
        f"{sev['critical'][0]} críticas, {sev['serious'][0]} sérias, {sev['moderate'][0]} moderadas e {sev['minor'][0]} leves. "
        f"As {sev['critical'][0] + sev['serious'][0]} regras críticas e sérias bloqueiam diretamente os perfis descritos no case "
        "(usuários de leitor de tela e de teclado). Principais problemas: " +
        esc("; ".join(r.titulo for r in rel.violacoes if r.impacto in ("critical", "serious"))) + "."))
    if depois is not None:
        s.append(Spacer(1, 4))
        s.append(P(
            f"Após as correções no HTML, a nova varredura retornou <b>{len(depois.violacoes)} violações</b> "
            f"({depois.passes} regras aprovadas contra {rel.passes} na versão original). "
            "O resultado automatizado, porém, cobre apenas uma parte dos problemas de acessibilidade; "
            "a seção 6 lista as verificações manuais que ainda precisam ser feitas."))
    s += [Spacer(1, 8), grafico(), Spacer(1, 4)]
    s.append(tabela([cab("Indicador", "Valor"),
                     [P("Regras violadas"), P(str(len(rel.violacoes)))],
                     [P("Elementos afetados"), P(str(rel.total_elementos))],
                     [P("Regras aprovadas"), P(str(rel.passes))],
                     [P("A revisar manualmente (needs review)"), P(str(len(rel.revisar)))],
                     [P("Violações de nível A / AA / boa prática"),
                      P(f"{rel.por_nivel()['A']} / {rel.por_nivel()['AA']} / {rel.por_nivel()['Boa prática']}")]],
                    [10 * cm, 7 * cm]))

    # ---- 2. Ambiente e metodologia ----------------------------------------
    s.append(P("2. Ambiente e metodologia", "h1"))
    prova_vscode = Path("evidencias/execucao_axe_cli_vscode.txt").is_file()   # só afirma "VS Code" se houver a evidência
    s.append(P(("A auditoria da página original foi executada no terminal integrado do VS Code (npm run audit; log e print em "
                "evidencias/execucao_axe_cli_vscode.txt e captura_terminal_vscode.png)" if prova_vscode else
                "A auditoria foi executada pela linha de comando (os mesmos comandos estão em package.json e em .vscode/tasks.json, "
                "para rodar no terminal do VS Code)") +
               " sobre o <font name='Courier'>index.html</font> servido localmente. "
               "O axe gerou o arquivo JSON, que o script Python lê, classifica e converte neste relatório."))
    s.append(Spacer(1, 4))
    s.append(tabela([cab("Item", "Detalhe"),
                     [P("Motor de análise"), P(esc(rel.motor))],
                     [P("Conjunto de regras (tags)"), P(esc(", ".join(rel.tags)))],
                     [P("Navegador"), P(esc(rel.ambiente))],
                     [P("Servidor local"), P("python -m http.server 8080")],
                     [P("Extensões recomendadas (VS Code)"), P("Live Server, axe Accessibility Linter e Python (.vscode/extensions.json)")],
                     [P("Comando de auditoria"), P("axe http://localhost:8080/index.html --tags wcag2a,wcag2aa,wcag21a,wcag21aa,best-practice --save resultado_axe.json", "cod")],
                     [P("Comando do relatório"), P("python parser.py resultado_axe.json --corrigido resultado_axe_corrigido.json", "cod")]],
                    [4.5 * cm, L - 4.5 * cm]))
    s.append(Spacer(1, 4))
    s.append(P("<b>Severidade (campo <i>impact</i> do axe):</b> crítica = bloqueia o acesso; séria = causa grande dificuldade; "
               "moderada = causa alguma dificuldade; leve = incômodo. A priorização ordena por severidade e, em seguida, "
               "pela quantidade de elementos afetados.", "peq"))

    # ---- 3. Resultados consolidados ---------------------------------------
    s.append(P("3. Violações encontradas e mapeamento WCAG 2.1", "h1"))
    linhas = [cab("#", "Regra (axe)", "Problema", "Severidade", "WCAG", "Nível", "Elem.")]
    for i, r in enumerate(rel.violacoes, 1):
        linhas.append([P(str(i), "celula"), P(f"<font name='Courier'>{esc(r.id)}</font>", "celula"), P(esc(r.titulo), "celula"),
                       P(f"<font color='{COR_PDF[r.impacto]}'><b>{ROTULO[r.impacto]}</b></font>", "celula"),
                       P(esc(r.criterios_txt), "celula"), P(r.nivel, "celula"), P(str(len(r.elementos)), "celula")])
    s.append(tabela(linhas, [0.8 * cm, 3.5 * cm, 5.4 * cm, 1.9 * cm, 2.4 * cm, 1.8 * cm, 1.2 * cm]))
    s.append(Spacer(1, 4))
    s.append(P("Regras de <i>boa prática</i> não correspondem a um critério de sucesso da WCAG; para elas a coluna WCAG mostra "
               "o critério mais próximo (\"relac.\").", "peq"))

    mapa = agrupar_por_criterio(rel.violacoes)
    s.append(P("Critérios de sucesso da WCAG 2.1 afetados", "h2"))
    linhas = [cab("Critério", "Nome", "Nível", "Regras do axe")]
    for numero, regras in mapa.items():
        nome, nivel = WCAG_21.get(numero, ("-", "?"))
        linhas.append([P(numero, "celula"), P(esc(nome), "celula"), P(nivel, "celula"),
                       P(esc(", ".join(r.id for r in regras)), "celula")])
    s.append(tabela(linhas, [1.8 * cm, 6.2 * cm, 1.4 * cm, 7.6 * cm]))

    # ---- 4. Diagnóstico detalhado -----------------------------------------
    s.append(PageBreak())
    s.append(P("4. Diagnóstico detalhado e plano de remediação", "h1"))
    s.append(P("Cada regra traz o impacto para o usuário, os elementos afetados (seletor CSS e trecho do HTML) e a correção recomendada."))
    for i, r in enumerate(rel.violacoes, 1):
        crit = "; ".join(f"{n} {nome} (nível {nv})" for n, nome, nv in r.criterios) or \
               ("Boa prática do axe (relacionada a " + ", ".join(r.relacionados) + ")" if r.relacionados else "Boa prática do axe")
        cabecalho = Table([[P(f"<font color='white'><b>{i}. {esc(r.titulo)}</b></font>"),
                            P(f"<font color='white'><b>{ROTULO[r.impacto].upper()}</b></font>")]],
                          colWidths=[L - 3 * cm, 3 * cm])
        cabecalho.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(COR_PDF[r.impacto])),
                                       ("ALIGN", (1, 0), (1, 0), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        linhas = [cab("Seletor CSS", "Trecho do HTML")]
        for el in r.elementos:
            html = el.html if len(el.html) <= 150 else el.html[:147] + "..."
            if el.detalhe:
                html += f"  [{el.detalhe}]"
            linhas.append([P(esc(el.seletor), "cod"), P(esc(html), "cod")])
        bloco = [cabecalho, Spacer(1, 3),
                 P(f"<b>Regra:</b> <font name='Courier'>{esc(r.id)}</font> &nbsp;|&nbsp; <b>WCAG:</b> {esc(crit)}", "peq"),
                 P(f"<b>Impacto:</b> {esc(r.por_que)}", "peq"),
                 P(f"<b>Como corrigir:</b> {esc(r.correcao)}", "peq"), Spacer(1, 3)]
        for f in bloco:                       # cabeçalho e texto ficam junto do início da tabela
            f.keepWithNext = 1
        s += bloco + [tabela(linhas, [5.6 * cm, L - 5.6 * cm])]
        s.append(Spacer(1, 10))

    # ---- 5. Revisão manual do axe -----------------------------------------
    if rel.revisar:
        s.append(P("5. Itens que o axe não conseguiu decidir (needs review)", "h1"))
        for r in rel.revisar:
            s.append(P(f"<b>{esc(r.titulo)}</b> (<font name='Courier'>{esc(r.id)}</font>, {ROTULO[r.impacto].lower()}): "
                       f"{esc(r.por_que)} <i>Elementos:</i> " + esc(", ".join(e.seletor for e in r.elementos)) +
                       f". <b>Ação:</b> {esc(r.correcao)}" +
                       (f" <b>Nota:</b> {esc(NOTAS_REVISAR[r.id])}" if r.id in NOTAS_REVISAR else "")))

    # ---- 6. Verificações manuais ------------------------------------------
    s.append(P("6. Verificações manuais recomendadas", "h1"))
    s.append(P("Ferramentas automáticas detectam apenas parte das barreiras. Os itens abaixo foram identificados por inspeção "
               "do código (não pela ferramenta) e devem ser conferidos manualmente."))
    s.append(Spacer(1, 4))
    s.append(tabela([cab("Verificação", "O que conferir")] + [[P(f"<b>{esc(a)}</b>", "celula"), P(esc(b), "celula")]
                                                             for a, b in VERIFICACOES_MANUAIS], [5 * cm, L - 5 * cm]))

    # ---- 7. Reauditoria ----------------------------------------------------
    s.append(P("7. Resultado após a correção do HTML", "h1"))
    if depois is not None:
        linhas = [cab("Severidade", "Antes (regras / elementos)", "Depois (regras / elementos)")]
        for sv, ra, ea, rd, ed in comparar(rel, depois):
            linhas.append([P(f"<font color='{COR_PDF[sv]}'><b>{ROTULO[sv]}</b></font>"), P(f"{ra} / {ea}"), P(f"{rd} / {ed}")])
        linhas.append([P("<b>Total</b>"), P(f"<b>{len(rel.violacoes)} / {rel.total_elementos}</b>"),
                       P(f"<b>{len(depois.violacoes)} / {depois.total_elementos}</b>")])
        s.append(tabela(linhas, [4 * cm, 6.5 * cm, 6.5 * cm]))
        persistem = {r.id for r in depois.violacoes}
        s.append(Spacer(1, 6))
        s.append(P("<b>Situação de cada regra:</b> " + "; ".join(
            f"{esc(r.id)}: {'persiste' if r.id in persistem else 'resolvida'}" for r in rel.violacoes) + "."))
        if depois.revisar:
            s.append(Spacer(1, 4))
            s.append(P("<b>Para revisão manual na versão corrigida:</b> " + "; ".join(
                f"{esc(r.id)} em {esc(', '.join(e.seletor for e in r.elementos))}" for r in depois.revisar) + "."))
    else:
        s.append(P("Reauditoria não informada (use --corrigido resultado_axe_corrigido.json)."))

    if all(c and Path(c).is_file() for c in capturas):
        s.append(Spacer(1, 8))
        imgs = []
        for caminho, legenda in zip(capturas, ("Versão original (topo da página)", "Versão corrigida (topo da página)")):
            iw, ih = ImageReader(str(caminho)).getSize()
            largura = 8.2 * cm
            imgs.append([Image(str(caminho), width=largura, height=largura * ih / iw), P(f"<b>{legenda}</b>", "centro")])
        s.append(Table([[imgs[0][0], imgs[1][0]], [imgs[0][1], imgs[1][1]]], colWidths=[L / 2, L / 2]))

    # ---- Anexo A: HTML corrigido ------------------------------------------
    s.append(PageBreak())
    s.append(P("Anexo A - Código HTML corrigido (corrigido/index.html)", "h1"))
    if html_corrigido and html_corrigido.is_file():
        s.append(P("Os comentários <font name='Courier'>CORREÇÃO</font> indicam cada alteração feita em relação ao arquivo original."))
        s.append(Spacer(1, 4))
        s.append(Preformatted(_limpar(_quebrar_codigo(html_corrigido.read_text(encoding="utf-8"))), st["cod"]))
    else:
        s.append(P("Arquivo HTML corrigido não encontrado (use --html-corrigido)."))

    # ---- Anexo B: repositório e conclusão ---------------------------------
    s.append(P("Anexo B - Repositório e entregáveis", "h1"))
    s.append(tabela([cab("Entregável", "Arquivo"),
                     [P("Comprovação da execução do axe CLI"), P(("evidencias/execucao_axe_cli_vscode.txt (terminal do VS Code), " if prova_vscode else "") + "evidencias/ (logs do axe CLI e do parser, capturas de tela), package.json, .vscode/tasks.json", "cod")],
                     [P("Auditoria JSON"), P("resultado_axe.json (original) e resultado_axe_corrigido.json", "cod")],
                     [P("Script Python documentado"), P("parser.py / parser_acessibilidade.py", "cod")],
                     [P("HTML de teste e corrigido"), P("index.html e corrigido/index.html", "cod")],
                     [P("Projeto no GitHub"), P(esc(github), "cod")]], [6 * cm, L - 6 * cm]))
    s.append(P("Conclusão", "h1"))
    s.append(P("A integração do axe-core ao fluxo de desenvolvimento permite achar e priorizar rapidamente barreiras objetivas "
               "(texto alternativo, rótulos, nomes acessíveis, contraste, idioma, título e estrutura). Como a automação não "
               "substitui o teste com usuários e tecnologias assistivas, recomenda-se manter o script no pipeline com a opção "
               "<font name='Courier'>--falhar-se serious</font> e complementar com as verificações manuais da seção 6."))

    doc.build(s, onFirstPage=capa, onLaterPages=rodape)


# --------------------------------------------------------------------------- #
# Linha de comando
# --------------------------------------------------------------------------- #

def main(argv: list | None = None) -> int:
    """Ponto de entrada: lê os argumentos, executa as etapas 1 a 5 e devolve o código de saída (1 se --falhar-se for acionado)."""
    for fluxo in (sys.stdout, sys.stderr):                     # evita erro de acentuação no console do Windows
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")

    ap = argparse.ArgumentParser(description="Parser do relatório JSON do axe-core: resumo no terminal e relatório em PDF.")
    ap.add_argument("entrada", nargs="?", default="resultado_axe.json", help="JSON do axe (padrão: resultado_axe.json)")
    ap.add_argument("--corrigido", help="JSON do axe da versão corrigida (gera o comparativo antes x depois)")
    ap.add_argument("--html-corrigido", default="corrigido/index.html", help="HTML corrigido a incluir no PDF")
    ap.add_argument("--saida", default="relatorio/Relatorio_Acessibilidade_TechInclusiva.pdf", help="caminho do PDF")
    ap.add_argument("--github", default="https://github.com/<usuario>/<repositorio>", help="URL do repositório no GitHub")
    ap.add_argument("--integrantes", default="[Nome do integrante 1]; [Nome do integrante 2]; [Nome do integrante 3]",
                    help="nomes separados por ponto e vírgula")
    ap.add_argument("--captura-antes", default="evidencias/captura_original.png", help="PNG da página original (opcional)")
    ap.add_argument("--captura-depois", default="evidencias/captura_corrigida.png", help="PNG da página corrigida (opcional)")
    ap.add_argument("--html", action="store_true", help="mostra o trecho HTML de cada elemento no terminal")
    ap.add_argument("--sem-pdf", action="store_true", help="só imprime o resumo no terminal")
    ap.add_argument("--sem-cor", action="store_true", help="desliga cores ANSI")
    ap.add_argument("--falhar-se", choices=SEVERIDADES, help="código de saída 1 se houver violação desta severidade ou pior")
    args = ap.parse_args(argv)

    rel = extrair(carregar_json(args.entrada))
    depois = extrair(carregar_json(args.corrigido)) if args.corrigido else None
    imprimir_resumo(rel, depois, cor=sys.stdout.isatty() and not args.sem_cor, mostrar_html=args.html)

    if not args.sem_pdf:
        try:
            gerar_pdf(rel, depois, Path(args.saida), Path(args.html_corrigido), args.github,
                      [i.strip() for i in args.integrantes.split(";") if i.strip()],
                      (args.captura_antes, args.captura_depois))
        except ImportError:
            raise SystemExit("reportlab não instalado. Rode: pip install -r requirements.txt")
        print(f"PDF salvo em: {Path(args.saida).resolve()}")

    if args.falhar_se:
        limite = PESO[args.falhar_se]
        if any(PESO[r.impacto] >= limite for r in rel.violacoes):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

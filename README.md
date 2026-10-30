# Auditoria de Acessibilidade Web - Portal TechInclusiva

Trabalho Prático II da unidade **Qualidade e Teste de Software** (Prof. MSc. Hugo Menezes Barra).

Aluno: **Arthur Moreira Santos**

Solução híbrida para verificar a conformidade com a **WCAG 2.1 (níveis A e AA)**:

1. o **axe-core CLI** audita a página no terminal do VS Code e gera `resultado_axe.json`;
2. o script **Python** (`parser.py`) lê o JSON, classifica por severidade, mapeia cada falha para um critério da WCAG,
   lista os seletores afetados e gera o **relatório em PDF**.

## Resultado da auditoria

| Página | Regras violadas | Elementos afetados | Crítica | Séria | Moderada | Leve |
|---|---|---|---|---|---|---|
| `index.html` (original) | **22** | 51 | 6 | 10 | 4 | 2 |
| `corrigido/index.html` | **0** | 0 | 0 | 0 | 0 | 0 |

- Motor: axe-core 4.14.0 · tags `wcag2a, wcag2aa, wcag21a, wcag21aa, best-practice`
- A versão original tem ainda 1 item em *needs review* (`video-caption`, vídeo sem `<track>`).
- Automação cobre só parte dos problemas: veja as **verificações manuais** na seção 6 do PDF.

## Estrutura

```
index.html                         página de teste com barreiras de acessibilidade (versão original)
corrigido/index.html               versão corrigida (comentários "CORREÇÃO" indicam cada mudança)
mapa-redacao.html                  página exibida no iframe "Mapa da redação" (há uma cópia em corrigido/)
img/                               logo, ilustrações das notícias, pôster do vídeo e a foto teclado_adaptado.webp (cópia em corrigido/img/)
resultado_axe.json                 saída do axe CLI - página original
resultado_axe_corrigido.json       saída do axe CLI - página corrigida
parser.py                          ponto de entrada do script (entregável)
parser_acessibilidade.py           implementação documentada do parser
relatorio/Relatorio_Acessibilidade_TechInclusiva.pdf   relatório final
evidencias/                        log do terminal (axe CLI) e capturas de tela antes/depois
.vscode/                           extensões recomendadas e tarefas (servir, auditar, gerar relatório)
```

## Como executar (terminal do VS Code)

Pré-requisitos: Node.js 18+, Python 3.9+ e Google Chrome instalados.

```bash
npm install                  # axe-core CLI + chromedriver
pip install -r requirements.txt
```

Terminal 1 - servir o site (ou use a extensão *Live Server*, porta 5500):

```bash
python -m http.server 8080
```

Terminal 2 - auditar e gerar o relatório:

```bash
npm run audit                # gera resultado_axe.json (página original)
npm run audit:corrigido      # gera resultado_axe_corrigido.json
npm run report               # resumo no terminal + PDF em relatorio/
```

Também é possível usar *Terminal > Executar tarefa...* no VS Code (tarefas definidas em `.vscode/tasks.json`).

Opções úteis do parser:

```bash
python parser.py resultado_axe.json --html                      # mostra o trecho HTML de cada elemento
python parser.py resultado_axe.json --falhar-se serious         # retorna código 1 se houver falha séria ou pior (CI)
python parser.py resultado_axe.json --github https://github.com/naivepage13/Trabalho_II_Teste_Auditoria_Acessibilidade_Web --integrantes "Arthur Moreira Santos"
```

## Solução de problemas

- **`ChromeDriver ... does not match Chrome`**: a versão do `chromedriver` em `package.json` precisa ter o mesmo número
  principal do seu Chrome (veja em `chrome://version`). Ajuste e reinstale, por exemplo:
  `npm install -D chromedriver@<versão do seu Chrome>`. O `overrides` do `package.json` garante que o axe use essa mesma versão.
- **`spawn ... chromedriver.exe ENOENT` no Windows com pasta muito profunda**: o caminho do projeto passa de 260 caracteres.
  Mova o projeto para um caminho curto (ex.: `C:\projetos\techinclusiva`).

## Publicar no GitHub

Repositório: https://github.com/naivepage13/Trabalho_II_Teste_Auditoria_Acessibilidade_Web

Dentro da pasta do projeto (a que contém o `package.json`), no terminal:

```bash
git init
git branch -M main
git add .
git commit -m "Auditoria de acessibilidade do Portal TechInclusiva (axe-core + parser Python)"
git remote add origin https://github.com/naivepage13/Trabalho_II_Teste_Auditoria_Acessibilidade_Web.git
git push -u origin main
```

O `.gitignore` já exclui `node_modules/` e `__pycache__/`. Para atualizar o relatório depois de mudar algo: `npm run report`.

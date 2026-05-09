# Thronebound 👑

> Um RPG assíncrono de administração de nações e narrativa guiado por IA, jogado diretamente no Discord.

## Sobre o Projeto

**Thronebound** é um projeto de RPG *slow-burn* focado em interpretação de papéis (Roleplay) e estratégia de longo prazo. O objetivo do jogo é colocar os jogadores no controle de Soberanos de nações independentes, onde cada decisão política, militar ou diplomática afeta o ecossistema do mundo.

O grande diferencial do projeto é a utilização de Modelos de Linguagem (LLMs) locais para atuar como o Mestre do Jogo (GM) e controlar os NPCs. Em vez de depender de cálculos matemáticos rígidos para interações complexas, a IA lê as intenções do jogador e gera consequências narrativas dinâmicas baseadas na história do mundo e no estado atual da nação.

## Mecânicas Principais

* **Narrativa Dinâmica (IA como Mestre):** Interações com reinos vizinhos, respostas a crises e diálogos diplomáticos são processados e narrados por IA, garantindo que nenhuma partida seja igual a outra.
* **Tempo Real Assíncrono:** Ações têm peso. Enviar uma tropa ou um emissário exige tempo de viagem real (horas ou dias), desencorajando decisões impulsivas e forçando o planejamento estratégico.
* **Sistema de Linhagem:** O maior desafio do jogador não é apenas expandir, mas sobreviver. A morte do Soberano sem um herdeiro legítimo e previamente nomeado resulta em falha crítica (Game Over).
* **Memória Persistente:** Utilizando banco de dados vetorial (RAG), o mundo lembra dos seus decretos, alianças passadas e traições, mantendo a coerência narrativa a longo prazo.

## Stack Tecnológica

A arquitetura foi pensada para rodar de forma eficiente, flexível e modular, separando as regras de jogo (Engine) da geração de texto:

* **Backend / Lógica:** Python (`discord.py`, FastAPI para o Web Dashboard)
* **Memória de Longo Prazo (RAG):** ChromaDB
* **Estado e Regras (Matemática):** SQLite
* **Motor de IA (Agnóstico):** O jogo suporta arquitetura híbrida configurável via `.env`:
  * **Ollama (Privacidade/Local):** Executado em container Docker isolado, com suporte a passthrough de hardware (AMD ROCm / Nvidia) e auto-pull de modelos.
  * **Google Gemini API (Cloud/Velocidade):** Integração nativa para resoluções imediatas e alta capacidade de raciocínio.
* **Orquestração:** Docker & Docker Compose

## Estrutura de Arquitetura

1. O **Discord Bot** recebe as ações narrativas dos jogadores.
2. A lógica valida os recursos e o tempo de execução via **SQLite**.
3. O contexto histórico relevante é extraído via buscas de similaridade no **ChromaDB**.
4. O prompt montado é enviado ao **Ollama** (ex: modelo *Gemma 4*), que devolve a consequência da ação interpretada no formato de RPG.

---
*Este projeto está em fase de planejamento e desenvolvimento estrutural inicial.*

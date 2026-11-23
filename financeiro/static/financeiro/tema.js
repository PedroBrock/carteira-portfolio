'use strict';
// Substitui o admin/js/theme.js do Django: o botao alterna so entre claro e escuro (sem o modo "automatico").
// Na primeira visita (ou se ficou "auto" salvo de antes) segue o tema do sistema operacional.
{
    function temaDoSistema() {
        return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }

    function aplicarTema(tema) {
        document.documentElement.dataset.theme = tema;
        localStorage.setItem("theme", tema);
    }

    function alternarTema() {
        aplicarTema(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
    }

    const salvo = localStorage.getItem("theme");
    aplicarTema(salvo === "light" || salvo === "dark" ? salvo : temaDoSistema());

    window.addEventListener("load", function () {
        Array.from(document.getElementsByClassName("theme-toggle")).forEach((botao) => {
            botao.addEventListener("click", alternarTema);
        });
        // Celular e tablet: o botao da barra do topo abre o menu lateral como gaveta
        const botaoMenu = document.querySelector(".abrir-menu");
        const lateral = document.getElementById("menu-lateral");
        if (botaoMenu && lateral) {
            const raiz = document.documentElement;
            const abrir = (aberto) => {
                raiz.classList.toggle("menu-aberto", aberto);
                botaoMenu.setAttribute("aria-expanded", aberto ? "true" : "false");
                if (aberto) {
                    (lateral.querySelector(".atual a") || lateral.querySelector("a")).focus();
                }
            };
            botaoMenu.addEventListener("click", () => abrir(!raiz.classList.contains("menu-aberto")));
            // Clique fora do menu (no fundo escurecido) ou Esc fecham
            document.addEventListener("click", (e) => {
                if (raiz.classList.contains("menu-aberto") && !lateral.contains(e.target) && !botaoMenu.contains(e.target)) {
                    abrir(false);
                }
            });
            document.addEventListener("keydown", (e) => {
                if (e.key === "Escape" && raiz.classList.contains("menu-aberto")) {
                    abrir(false);
                    botaoMenu.focus();
                }
            });
        }
    });
}

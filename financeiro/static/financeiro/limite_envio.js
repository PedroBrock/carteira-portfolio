// Avisa antes de enviar quando os arquivos passam do limite do formulario (data-limite-mb).
// Na Vercel um envio acima de 4,5 MB e recusado antes de chegar ao sistema, com uma pagina de erro em ingles.
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll("form[data-limite-mb]").forEach(function (form) {
    var limite = parseFloat(form.dataset.limiteMb) * 1024 * 1024;
    form.addEventListener("submit", function (evento) {
      var total = 0;
      form.querySelectorAll("input[type=file]").forEach(function (campo) {
        Array.prototype.forEach.call(campo.files, function (arquivo) { total += arquivo.size; });
      });
      if (total > limite) {
        evento.preventDefault();
        var mb = (total / 1024 / 1024).toFixed(1).replace(".", ",");
        alert("Os arquivos somam " + mb + " MB e o limite por envio é " + form.dataset.limiteMb +
          " MB. Envie em partes (menos arquivos de cada vez).");
      }
    });
  });
});

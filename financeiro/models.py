from decimal import Decimal

from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Case, Count, Q, Sum, Value, When
from django.utils import timezone

from .regras import Situacao, calcular_situacao, um_mes_depois
from .validadores import formatar_cep, formatar_cpf_cnpj, validar_cep, validar_cpf_cnpj, validar_uf


class Cliente(models.Model):
    # Dados que vao no boleto como "pagador" (a Caixa exige todos para registrar o boleto).
    CAMPOS_PAGADOR = ["nome", "cpf_cnpj", "endereco", "bairro", "cidade", "uf", "cep"]

    nome = models.CharField(max_length=150)
    cpf_cnpj = models.CharField("CPF/CNPJ", max_length=18, blank=True, validators=[validar_cpf_cnpj])
    email = models.EmailField(blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    endereco = models.CharField("endereço", max_length=200, blank=True, help_text="Rua, número e complemento.")
    bairro = models.CharField(max_length=80, blank=True)
    cidade = models.CharField(max_length=80, blank=True)
    uf = models.CharField("UF", max_length=2, blank=True, validators=[validar_uf])
    cep = models.CharField("CEP", max_length=9, blank=True, validators=[validar_cep])
    observacoes = models.TextField("observações", blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome"]

    def __str__(self):
        return self.nome

    def save(self, *args, **kwargs):
        self.cpf_cnpj = formatar_cpf_cnpj(self.cpf_cnpj)
        self.cep = formatar_cep(self.cep)
        self.uf = (self.uf or "").upper()
        super().save(*args, **kwargs)

    def dados_pagador(self):
        return {campo: getattr(self, campo) for campo in self.CAMPOS_PAGADOR}

    @property
    def cadastro_completo(self):
        """Tem todos os dados que a Caixa exige do pagador para registrar/alterar boletos."""
        return all(self.dados_pagador().values())


class Financiamento(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="financiamentos")
    descricao = models.CharField(
        "descrição", max_length=150, blank=True, help_text="Ex.: unidade, lote, empreendimento."
    )
    numero_documento = models.CharField("nº documento", max_length=20, blank=True)
    total_parcelas = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Vazio para títulos avulsos (entradas, intermediárias)."
    )
    data_entrada = models.DateField(null=True, blank=True)
    observacoes = models.TextField("observações", blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["cliente__nome", "data_entrada"]

    def __str__(self):
        base = self.descricao or ("Títulos avulsos" if self.total_parcelas is None else f"{self.total_parcelas} parcelas")
        return f"{self.cliente} - {base}"

    def resumo(self, hoje=None):
        """Totais (quantidade e valor) por situacao."""
        linhas = self.parcelas.com_situacao(hoje).values("situacao").annotate(qtd=Count("id"), total=Sum("valor"))
        return {linha["situacao"]: linha for linha in linhas}


class ParcelaQuerySet(models.QuerySet):
    def com_situacao(self, hoje=None):
        """Anota o campo `situacao` calculado no banco (mesma regra de regras.calcular_situacao)."""
        hoje = hoje or timezone.localdate()
        return self.annotate(
            situacao=Case(
                When(data_pagamento__isnull=False, then=Value(Situacao.PAGO)),
                When(requer_verificacao=True, then=Value(Situacao.VERIFICAR)),
                When(data_limite__lt=hoje, then=Value(Situacao.VENCIDO)),
                default=Value(Situacao.A_VENCER),
                output_field=models.CharField(max_length=10),
            )
        )

    def pagas(self):
        return self.filter(data_pagamento__isnull=False)

    def vencidas(self, hoje=None):
        return self.com_situacao(hoje).filter(situacao=Situacao.VENCIDO)

    def a_vencer(self, hoje=None):
        return self.com_situacao(hoje).filter(situacao=Situacao.A_VENCER)

    def a_verificar(self):
        return self.filter(data_pagamento__isnull=True, requer_verificacao=True)


class Parcela(models.Model):
    financiamento = models.ForeignKey(Financiamento, on_delete=models.CASCADE, related_name="parcelas")
    numero = models.PositiveSmallIntegerField("nº", null=True, blank=True)
    nosso_numero = models.CharField("nosso número", max_length=20, unique=True, null=True, blank=True)
    valor = models.DecimalField(max_digits=12, decimal_places=2)
    vencimento = models.DateField()
    data_limite = models.DateField(
        editable=False, help_text="Vencimento + 1 mês. Depois desta data a parcela não paga fica vencida."
    )
    data_pagamento = models.DateField("data de pagamento", null=True, blank=True)
    requer_verificacao = models.BooleanField(
        "baixado - verificar", default=False, help_text="Baixada pelo banco antes de vencer; precisa de análise."
    )
    situacao_banco = models.CharField("situação no banco", max_length=40, blank=True)
    ultimo_comando_banco = models.DateField("último comando no banco", null=True, blank=True)
    observacoes = models.TextField("observações", blank=True)

    objects = ParcelaQuerySet.as_manager()

    class Meta:
        ordering = ["financiamento", "vencimento", "numero"]
        indexes = [models.Index(fields=["data_limite"]), models.Index(fields=["vencimento"])]
        constraints = [
            models.UniqueConstraint(
                fields=["financiamento", "numero"],
                condition=Q(numero__isnull=False),
                name="parcela_numero_unico_por_financiamento",
            )
        ]

    def __str__(self):
        return f"{self.financiamento.cliente} - {self.rotulo_numero} - {self.vencimento:%d/%m/%Y}"

    @property
    def rotulo_numero(self):
        if self.numero is None:
            return "avulso"
        return f"{self.numero}/{self.financiamento.total_parcelas or '?'}"

    def save(self, *args, **kwargs):
        self.data_limite = um_mes_depois(self.vencimento)
        if kwargs.get("update_fields") is not None and "vencimento" in kwargs["update_fields"]:
            kwargs["update_fields"] = {*kwargs["update_fields"], "data_limite"}
        super().save(*args, **kwargs)

    def calcular_situacao(self, hoje=None):
        return calcular_situacao(
            vencimento=self.vencimento,
            data_pagamento=self.data_pagamento,
            requer_verificacao=self.requer_verificacao,
            hoje=hoje or timezone.localdate(),
        )


class TituloExcluido(models.Model):
    """Nosso numero de uma parcela excluida (ex.: erro de lancamento).

    A importacao do extrato ignora esses titulos, para que a parcela nao volte
    quando o mesmo extrato (ou um mais novo) for importado de novo.
    """

    nosso_numero = models.CharField("nosso número", max_length=20, unique=True)
    cliente = models.CharField(max_length=150, blank=True)
    descricao = models.CharField("descrição", max_length=200, blank=True)
    excluido_em = models.DateTimeField("excluído em", auto_now_add=True)

    class Meta:
        verbose_name = "título excluído"
        verbose_name_plural = "títulos excluídos"
        ordering = ["-excluido_em"]

    def __str__(self):
        return f"{self.nosso_numero} - {self.cliente}"


class Atividade(models.Model):
    """Eventos que o historico do admin (LogEntry) nao registra: acessos e importacoes.

    As alteracoes de cadastros (criar/alterar/excluir) ficam no LogEntry do Django;
    a tela "Registro de atividades" mostra os dois juntos.
    """

    class Tipo(models.TextChoices):
        LOGIN = "LOGIN", "Entrou"
        LOGOUT = "LOGOUT", "Saiu"
        LOGIN_FALHOU = "LOGIN_FALHOU", "Login falhou"
        IMPORTACAO = "IMPORTACAO", "Importou extrato"

    data = models.DateTimeField(auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="atividades"
    )
    usuario_nome = models.CharField("usuário", max_length=150, blank=True)
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    descricao = models.TextField("descrição", blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)

    class Meta:
        ordering = ["-data"]

    def __str__(self):
        return f"{self.data:%d/%m/%Y %H:%M} {self.usuario_nome} {self.get_tipo_display()}"


class PendenciaCaixa(models.Model):
    """Alteracao feita no sistema que tambem precisa ser feita na cobranca da Caixa.

    Criada automaticamente (ver pendencias.py). O envio e pelo arquivo de remessa CNAB 240
    (remessa.py, acao "Gerar arquivo de remessa" no admin) ou manual no e-Cobranca, marcando como enviada.
    """

    class Tipo(models.TextChoices):
        BAIXA = "BAIXA", "Pedido de baixa"
        ALTERAR_VENCIMENTO = "ALTERAR_VENCIMENTO", "Alterar vencimento"
        ALTERAR_VALOR = "ALTERAR_VALOR", "Alterar valor"
        ALTERAR_PAGADOR = "ALTERAR_PAGADOR", "Alterar dados do pagador"

    class Status(models.TextChoices):
        PENDENTE = "PENDENTE", "Pendente"
        ENVIADA = "ENVIADA", "Enviada à Caixa"
        CONFIRMADA = "CONFIRMADA", "Confirmada pelo extrato"
        CANCELADA = "CANCELADA", "Cancelada"

    parcela = models.ForeignKey(
        Parcela, on_delete=models.SET_NULL, null=True, blank=True, related_name="pendencias_caixa"
    )
    nosso_numero = models.CharField("nosso número", max_length=20)
    cliente = models.CharField(max_length=150)
    descricao_parcela = models.CharField("parcela", max_length=100, blank=True)
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    motivo = models.CharField(max_length=200, blank=True)
    antes = models.JSONField(default=dict, blank=True)
    depois = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE)
    criado_em = models.DateTimeField("criada em", auto_now_add=True)
    criado_por = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="criada por"
    )
    atualizado_em = models.DateTimeField(auto_now=True)
    enviado_em = models.DateTimeField("enviada em", null=True, blank=True)
    enviado_por = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="enviada por"
    )
    remessa = models.ForeignKey(
        "RemessaCaixa", on_delete=models.SET_NULL, null=True, blank=True, related_name="pendencias"
    )
    # Dados do titulo quando a pendencia foi criada: a remessa usa se a parcela for excluida.
    titulo = models.JSONField(default=dict, blank=True, editable=False)

    class Meta:
        verbose_name = "alteração pendente para a Caixa"
        verbose_name_plural = "alterações pendentes para a Caixa"
        ordering = ["-criado_em"]
        indexes = [models.Index(fields=["status", "tipo"])]

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.cliente} - {self.nosso_numero}"


class ConvenioCaixa(models.Model):
    """Dados do convenio de cobranca na Caixa, usados no arquivo de remessa (um unico registro)."""

    agencia = models.CharField("agência", max_length=4, validators=[RegexValidator(r"^\d{4}$", "Informe 4 dígitos.")])
    dv_agencia = models.CharField(
        "dígito da agência", max_length=1, blank=True, help_text="Informado pela Caixa (vai no arquivo de remessa)."
    )
    codigo_beneficiario = models.CharField(
        "código do beneficiário",
        max_length=7,
        validators=[RegexValidator(r"^\d{7}$", "Informe os 7 dígitos do código (sem o dígito verificador).")],
    )
    cpf_cnpj = models.CharField("CPF/CNPJ do beneficiário", max_length=18, blank=True, validators=[validar_cpf_cnpj])
    nome = models.CharField("nome da empresa", max_length=30, blank=True, help_text="Como está no convênio da Caixa.")
    ambiente_teste = models.BooleanField(
        "em teste na Caixa",
        default=True,
        help_text="Enquanto a Caixa não liberar a produção, as remessas saem como REMESSA-TESTE e "
        "não alteram nada no banco (as pendências continuam pendentes).",
    )
    especie_titulo = models.CharField(
        "espécie do título", max_length=2, default="02", help_text="Código da espécie (02 = DM, 99 = outros)."
    )
    juros_codigo = models.CharField(
        "juros de mora",
        max_length=1,
        choices=[("3", "Isento"), ("1", "Valor por dia"), ("2", "Taxa mensal")],
        default="3",
    )
    juros_valor = models.DecimalField(
        "valor/taxa dos juros", max_digits=13, decimal_places=2, default=Decimal("0"),
        help_text="Valor em R$ por dia ou % ao mês, conforme a opção acima.",
    )
    multa_codigo = models.CharField(
        "multa",
        max_length=1,
        choices=[("0", "Sem multa"), ("1", "Valor fixo"), ("2", "Percentual")],
        default="0",
        help_text="Cobrada a partir do dia seguinte ao vencimento.",
    )
    multa_valor = models.DecimalField(
        "valor/percentual da multa", max_digits=13, decimal_places=2, default=Decimal("0"),
        help_text="Valor em R$ ou % sobre o valor do boleto, conforme a opção acima.",
    )
    protesto_codigo = models.CharField(
        "protesto", max_length=1, choices=[("3", "Não protestar"), ("1", "Protestar")], default="3"
    )
    protesto_dias = models.PositiveSmallIntegerField("dias para protesto", default=0)
    devolucao_dias = models.PositiveSmallIntegerField(
        "dias para baixa/devolução", default=30, help_text="Dias corridos depois do vencimento."
    )

    class Meta:
        verbose_name = "convênio Caixa"
        verbose_name_plural = "convênio Caixa"

    def __str__(self):
        return f"{self.agencia} / {self.codigo_beneficiario}"

    def save(self, *args, **kwargs):
        self.cpf_cnpj = formatar_cpf_cnpj(self.cpf_cnpj)
        super().save(*args, **kwargs)

    def faltando(self) -> list[str]:
        """Campos obrigatorios para gerar remessa que ainda nao foram preenchidos."""
        campos = {"dv_agencia": "dígito da agência", "cpf_cnpj": "CPF/CNPJ do beneficiário", "nome": "nome da empresa"}
        faltando = [rotulo for campo, rotulo in campos.items() if not getattr(self, campo)]
        if self.juros_codigo != "3" and not self.juros_valor:
            faltando.append("valor dos juros")
        if self.multa_codigo != "0" and not self.multa_valor:
            faltando.append("valor da multa")
        return faltando


class RemessaCaixa(models.Model):
    """Arquivo de remessa CNAB 240 gerado a partir das pendencias (ver remessa.py)."""

    nsa = models.PositiveIntegerField("nº sequencial (NSA)", unique=True)
    criado_em = models.DateTimeField("gerada em", auto_now_add=True)
    criado_por = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+", verbose_name="gerada por"
    )
    teste = models.BooleanField("remessa de teste", default=False)
    quantidade = models.PositiveIntegerField("títulos", default=0)
    conteudo = models.TextField("conteúdo", editable=False)

    class Meta:
        verbose_name = "remessa para a Caixa"
        verbose_name_plural = "remessas para a Caixa"
        ordering = ["-nsa"]

    def __str__(self):
        return f"Remessa {self.nsa}{' (teste)' if self.teste else ''}"

    @property
    def nome_arquivo(self):
        return f"remessa_{self.nsa:06d}.rem"

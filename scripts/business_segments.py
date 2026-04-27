# -*- coding: utf-8 -*-
"""
Categorias de negocio para Prospeccao B2B (Google Places).
Portado de /opt/ia-callcenter/frontend/src/pages/Prospecting.tsx (BUSINESS_SEGMENTS).
Mantemos a mesma hierarquia para reaproveitar a logica validada pelo Chefe.
"""

BUSINESS_SEGMENTS = {
    "saude_clinicas": {
        "label": "Saude - Clinicas",
        "types": [
            {"value": "clinica acupuntura", "label": "Clinica de Acupuntura"},
            {"value": "clinica alergia imunologia", "label": "Clinica de Alergia e Imunologia"},
            {"value": "clinica angiologia vascular", "label": "Clinica de Angiologia"},
            {"value": "clinica cardiologia", "label": "Clinica de Cardiologia"},
            {"value": "clinica cirurgia geral", "label": "Clinica de Cirurgia Geral"},
            {"value": "clinica cirurgia plastica", "label": "Clinica de Cirurgia Plastica"},
            {"value": "clinica dermatologia", "label": "Clinica de Dermatologia"},
            {"value": "clinica endocrinologia", "label": "Clinica de Endocrinologia"},
            {"value": "clinica fertilidade reproducao", "label": "Clinica de Fertilidade"},
            {"value": "clinica fisioterapia", "label": "Clinica de Fisioterapia"},
            {"value": "clinica fonoaudiologia", "label": "Clinica de Fonoaudiologia"},
            {"value": "clinica gastroenterologia", "label": "Clinica de Gastroenterologia"},
            {"value": "clinica geriatria", "label": "Clinica de Geriatria"},
            {"value": "clinica ginecologia obstetricia", "label": "Clinica de Ginecologia e Obstetricia"},
            {"value": "clinica hematologia", "label": "Clinica de Hematologia"},
            {"value": "clinica homeopatia", "label": "Clinica de Homeopatia"},
            {"value": "clinica implante dentario", "label": "Clinica de Implante Dentario"},
            {"value": "clinica infectologia", "label": "Clinica de Infectologia"},
            {"value": "clinica mastologia", "label": "Clinica de Mastologia"},
            {"value": "clinica medicina chinesa", "label": "Clinica de Medicina Chinesa"},
            {"value": "clinica medicina do trabalho", "label": "Clinica de Medicina do Trabalho"},
            {"value": "clinica medicina esportiva", "label": "Clinica de Medicina Esportiva"},
            {"value": "clinica medicina integrativa", "label": "Clinica de Medicina Integrativa"},
            {"value": "clinica nefrologia", "label": "Clinica de Nefrologia"},
            {"value": "clinica neurologia", "label": "Clinica de Neurologia"},
            {"value": "clinica nutricao nutricionista", "label": "Clinica de Nutricao"},
            {"value": "clinica odontologica dentista", "label": "Clinica Odontologica"},
            {"value": "clinica oftalmologia", "label": "Clinica de Oftalmologia"},
            {"value": "clinica oncologia", "label": "Clinica de Oncologia"},
            {"value": "clinica ortopedia traumatologia", "label": "Clinica de Ortopedia"},
            {"value": "clinica ortodontia", "label": "Clinica de Ortodontia"},
            {"value": "clinica otorrinolaringologia", "label": "Clinica de Otorrinolaringologia"},
            {"value": "clinica pediatria", "label": "Clinica de Pediatria"},
            {"value": "clinica pneumologia", "label": "Clinica de Pneumologia"},
            {"value": "clinica popular", "label": "Clinica Popular"},
            {"value": "clinica proctologia", "label": "Clinica de Proctologia"},
            {"value": "clinica psicologia", "label": "Clinica de Psicologia"},
            {"value": "clinica psiquiatria", "label": "Clinica de Psiquiatria"},
            {"value": "clinica radiologia diagnostico imagem", "label": "Clinica de Radiologia / Diagnostico por Imagem"},
            {"value": "clinica reabilitacao", "label": "Clinica de Reabilitacao"},
            {"value": "clinica reumatologia", "label": "Clinica de Reumatologia"},
            {"value": "clinica terapia chinesa", "label": "Clinica de Terapia Chinesa"},
            {"value": "clinica urologia", "label": "Clinica de Urologia"},
            {"value": "clinica geral medica", "label": "Clinica Geral / Clinica Medica"},
            {"value": "consultorio medico", "label": "Consultorio Medico"},
            {"value": "consultorio odontologico", "label": "Consultorio Odontologico"},
        ],
    },
    "saude_outros": {
        "label": "Saude - Geral",
        "types": [
            {"value": "casa de repouso lar idosos", "label": "Casa de Repouso / Lar de Idosos"},
            {"value": "centro de diagnostico", "label": "Centro de Diagnostico"},
            {"value": "centro de hemodialise", "label": "Centro de Hemodialise"},
            {"value": "clinica veterinaria", "label": "Clinica Veterinaria"},
            {"value": "drogaria farmacia", "label": "Drogaria / Farmacia"},
            {"value": "farmacia de manipulacao", "label": "Farmacia de Manipulacao"},
            {"value": "hospital", "label": "Hospital"},
            {"value": "laboratorio analises clinicas", "label": "Laboratorio de Analises Clinicas"},
            {"value": "laboratorio diagnostico imagem", "label": "Laboratorio de Imagem"},
            {"value": "loja de produtos naturais", "label": "Loja de Produtos Naturais"},
            {"value": "loja ortopedica", "label": "Loja de Produtos Ortopedicos"},
            {"value": "loja suplementos vitaminas", "label": "Loja de Suplementos"},
            {"value": "optica otica loja oculos", "label": "Optica"},
            {"value": "plano de saude", "label": "Plano de Saude"},
            {"value": "pronto socorro emergencia", "label": "Pronto Socorro"},
            {"value": "terapia ocupacional", "label": "Terapia Ocupacional"},
            {"value": "unidade basica saude UBS", "label": "UBS / Posto de Saude"},
        ],
    },
    "estetica": {
        "label": "Estetica e Beleza",
        "types": [
            {"value": "barbearia barber", "label": "Barbearia"},
            {"value": "centro estetica", "label": "Centro de Estetica"},
            {"value": "clinica bronzeamento", "label": "Clinica de Bronzeamento"},
            {"value": "clinica depilacao laser", "label": "Clinica de Depilacao a Laser"},
            {"value": "clinica estetica", "label": "Clinica de Estetica"},
            {"value": "clinica harmonizacao facial", "label": "Clinica de Harmonizacao Facial"},
            {"value": "clinica implante capilar", "label": "Clinica de Implante Capilar"},
            {"value": "clinica massagem massoterapia", "label": "Clinica de Massagem / Massoterapia"},
            {"value": "clinica rejuvenescimento", "label": "Clinica de Rejuvenescimento"},
            {"value": "design sobrancelhas", "label": "Design de Sobrancelhas"},
            {"value": "extensao cilios", "label": "Extensao de Cilios"},
            {"value": "micropigmentacao", "label": "Estudio de Micropigmentacao"},
            {"value": "studio maquiagem", "label": "Estudio de Maquiagem"},
            {"value": "studio unhas esmalteria", "label": "Esmalteria / Studio de Unhas"},
            {"value": "nail designer manicure", "label": "Nail Designer / Manicure"},
            {"value": "salao de beleza cabeleireiro", "label": "Salao de Beleza"},
            {"value": "spa day spa", "label": "SPA / Day SPA"},
        ],
    },
    "fitness": {
        "label": "Fitness e Esporte",
        "types": [
            {"value": "academia musculacao", "label": "Academia"},
            {"value": "box crossfit", "label": "Box de CrossFit"},
            {"value": "escola artes marciais luta", "label": "Escola de Artes Marciais"},
            {"value": "escola natacao piscina", "label": "Escola de Natacao"},
            {"value": "personal trainer", "label": "Personal Trainer"},
            {"value": "studio pilates", "label": "Studio de Pilates"},
            {"value": "studio yoga", "label": "Studio de Yoga"},
        ],
    },
    "imobiliario": {
        "label": "Imobiliario e Construcao",
        "types": [
            {"value": "administradora condominios", "label": "Administradora de Condominios"},
            {"value": "construtora construcao", "label": "Construtora"},
            {"value": "corretora imoveis corretor", "label": "Corretora de Imoveis"},
            {"value": "imobiliaria", "label": "Imobiliaria"},
            {"value": "incorporadora", "label": "Incorporadora"},
            {"value": "loja material construcao", "label": "Loja de Material de Construcao"},
            {"value": "loja material eletrico", "label": "Loja de Material Eletrico"},
            {"value": "loja material hidraulico", "label": "Loja de Material Hidraulico"},
            {"value": "loja pisos revestimentos", "label": "Loja de Pisos e Revestimentos"},
            {"value": "loja tintas", "label": "Loja de Tintas"},
            {"value": "marmoraria", "label": "Marmoraria"},
            {"value": "serralheria", "label": "Serralheria"},
            {"value": "vidracaria", "label": "Vidracaria"},
        ],
    },
    "alimentacao": {
        "label": "Alimentacao e Gastronomia",
        "types": [
            {"value": "acougue casa de carnes", "label": "Acougue / Casa de Carnes"},
            {"value": "adega loja de vinhos", "label": "Adega / Loja de Vinhos"},
            {"value": "bar boteco", "label": "Bar / Boteco"},
            {"value": "buffet eventos", "label": "Buffet / Casa de Eventos"},
            {"value": "cafeteria coffee shop", "label": "Cafeteria"},
            {"value": "casa de sucos", "label": "Casa de Sucos"},
            {"value": "churrascaria", "label": "Churrascaria"},
            {"value": "confeitaria bolos doces", "label": "Confeitaria"},
            {"value": "distribuidora bebidas", "label": "Distribuidora de Bebidas"},
            {"value": "food truck", "label": "Food Truck"},
            {"value": "hamburgueria", "label": "Hamburgueria"},
            {"value": "hortifruti verduras", "label": "Hortifruti / Sacolao"},
            {"value": "lanchonete", "label": "Lanchonete"},
            {"value": "mercado mercearia", "label": "Mercado / Mercearia"},
            {"value": "padaria panificadora", "label": "Padaria"},
            {"value": "pastelaria", "label": "Pastelaria"},
            {"value": "peixaria", "label": "Peixaria"},
            {"value": "pizzaria", "label": "Pizzaria"},
            {"value": "restaurante", "label": "Restaurante"},
            {"value": "restaurante japones sushi", "label": "Restaurante Japones / Sushi"},
            {"value": "restaurante vegano vegetariano", "label": "Restaurante Vegano / Vegetariano"},
            {"value": "rodizio", "label": "Rodizio"},
            {"value": "sorveteria", "label": "Sorveteria"},
            {"value": "supermercado", "label": "Supermercado"},
        ],
    },
    "lojas": {
        "label": "Lojas e Comercio",
        "types": [
            {"value": "antiquario", "label": "Antiquario"},
            {"value": "auto pecas", "label": "Auto Pecas"},
            {"value": "bicicletaria bike shop", "label": "Bicicletaria"},
            {"value": "boutique moda feminina", "label": "Boutique / Moda Feminina"},
            {"value": "colchoaria loja colchoes", "label": "Colchoaria"},
            {"value": "joalheria relojoaria", "label": "Joalheria / Relojoaria"},
            {"value": "loja artigos esportivos", "label": "Loja de Artigos Esportivos"},
            {"value": "loja brinquedos", "label": "Loja de Brinquedos"},
            {"value": "loja calcados sapatos", "label": "Loja de Calcados"},
            {"value": "loja celular acessorios", "label": "Loja de Celulares"},
            {"value": "loja cosmeticos perfumaria", "label": "Loja de Cosmeticos / Perfumaria"},
            {"value": "loja decoracao", "label": "Loja de Decoracao"},
            {"value": "loja eletrodomesticos", "label": "Loja de Eletrodomesticos"},
            {"value": "loja eletronicos", "label": "Loja de Eletronicos"},
            {"value": "loja games videogames", "label": "Loja de Games"},
            {"value": "loja informatica computadores", "label": "Loja de Informatica"},
            {"value": "loja moveis", "label": "Loja de Moveis"},
            {"value": "loja presentes artesanato", "label": "Loja de Presentes / Artesanato"},
            {"value": "loja roupas moda", "label": "Loja de Roupas"},
            {"value": "loja tecidos aviamentos", "label": "Loja de Tecidos"},
            {"value": "loja variedades bazar", "label": "Loja de Variedades / Bazar"},
            {"value": "magazine loja departamento", "label": "Magazine / Loja de Departamento"},
            {"value": "otica oculos", "label": "Otica"},
            {"value": "papelaria livraria", "label": "Papelaria / Livraria"},
            {"value": "pet shop", "label": "Pet Shop"},
            {"value": "sex shop", "label": "Sex Shop"},
            {"value": "tabacaria", "label": "Tabacaria"},
        ],
    },
    "escritorios": {
        "label": "Escritorios e Servicos",
        "types": [
            {"value": "agencia de emprego rh", "label": "Agencia de Emprego / RH"},
            {"value": "agencia de marketing digital", "label": "Agencia de Marketing Digital"},
            {"value": "agencia de publicidade propaganda", "label": "Agencia de Publicidade"},
            {"value": "agencia de turismo viagem", "label": "Agencia de Turismo / Viagem"},
            {"value": "assessoria contabil", "label": "Assessoria Contabil"},
            {"value": "assessoria de imprensa comunicacao", "label": "Assessoria de Imprensa"},
            {"value": "cartorio tabelionato", "label": "Cartorio / Tabelionato"},
            {"value": "consultoria empresarial", "label": "Consultoria Empresarial"},
            {"value": "consultoria financeira investimentos", "label": "Consultoria Financeira"},
            {"value": "contabilidade escritorio contabil", "label": "Contabilidade"},
            {"value": "corretora de seguros", "label": "Corretora de Seguros"},
            {"value": "despachante", "label": "Despachante"},
            {"value": "escritorio advocacia advogado", "label": "Escritorio de Advocacia"},
            {"value": "escritorio arquitetura design interiores", "label": "Escritorio de Arquitetura"},
            {"value": "escritorio coworking", "label": "Escritorio Coworking"},
            {"value": "escritorio engenharia", "label": "Escritorio de Engenharia"},
            {"value": "grafica impressao", "label": "Grafica"},
            {"value": "seguradora", "label": "Seguradora"},
            {"value": "studio design grafico", "label": "Estudio de Design Grafico"},
            {"value": "studio fotografia fotografo", "label": "Estudio de Fotografia"},
            {"value": "tradutor traducao", "label": "Servico de Traducao"},
        ],
    },
    "educacao": {
        "label": "Educacao",
        "types": [
            {"value": "autoescola centro formacao condutores", "label": "Autoescola"},
            {"value": "centro idiomas escola linguas", "label": "Centro de Idiomas"},
            {"value": "colegio escola ensino", "label": "Colegio / Escola"},
            {"value": "creche bercario", "label": "Creche / Bercario"},
            {"value": "curso preparatorio concurso vestibular", "label": "Curso Preparatorio"},
            {"value": "curso profissionalizante tecnico", "label": "Curso Profissionalizante"},
            {"value": "escola de danca", "label": "Escola de Danca"},
            {"value": "escola de musica", "label": "Escola de Musica"},
            {"value": "faculdade universidade", "label": "Faculdade / Universidade"},
            {"value": "reforco escolar aulas particulares", "label": "Reforco Escolar"},
        ],
    },
    "automotivo": {
        "label": "Automotivo",
        "types": [
            {"value": "auto eletrica eletricista automotivo", "label": "Auto Eletrica"},
            {"value": "borracharia pneus", "label": "Borracharia / Pneus"},
            {"value": "concessionaria veiculos", "label": "Concessionaria de Veiculos"},
            {"value": "estacionamento", "label": "Estacionamento"},
            {"value": "funilaria pintura automotiva", "label": "Funilaria e Pintura"},
            {"value": "lava rapido lava jato", "label": "Lava Rapido / Lava Jato"},
            {"value": "locadora veiculos aluguel carro", "label": "Locadora de Veiculos"},
            {"value": "oficina mecanica", "label": "Oficina Mecanica"},
            {"value": "retifica motores", "label": "Retifica de Motores"},
            {"value": "revenda carros usados seminovos", "label": "Revenda de Veiculos"},
        ],
    },
    "pets": {
        "label": "Pets e Veterinario",
        "types": [
            {"value": "banho tosa grooming pet", "label": "Banho e Tosa"},
            {"value": "clinica veterinaria pet", "label": "Clinica Veterinaria"},
            {"value": "hotel pet creche animal", "label": "Hotel para Pets"},
            {"value": "pet shop loja pet", "label": "Pet Shop"},
        ],
    },
    "tecnologia": {
        "label": "Tecnologia",
        "types": [
            {"value": "assistencia tecnica celular", "label": "Assistencia Tecnica de Celular"},
            {"value": "assistencia tecnica computador notebook", "label": "Assistencia Tecnica de Computador"},
            {"value": "desenvolvimento software", "label": "Empresa de Software / TI"},
            {"value": "provedor internet", "label": "Provedor de Internet"},
        ],
    },
    "servicos_casa": {
        "label": "Servicos para Casa",
        "types": [
            {"value": "chaveiro", "label": "Chaveiro"},
            {"value": "dedetizadora controle pragas", "label": "Dedetizadora"},
            {"value": "desentupidora", "label": "Desentupidora"},
            {"value": "eletricista instalacao eletrica", "label": "Eletricista"},
            {"value": "encanador instalacao hidraulica", "label": "Encanador"},
            {"value": "jardinagem paisagismo", "label": "Jardinagem / Paisagismo"},
            {"value": "lavanderia tinturaria", "label": "Lavanderia"},
            {"value": "limpeza residencial comercial", "label": "Empresa de Limpeza"},
            {"value": "marcenaria carpintaria", "label": "Marcenaria"},
            {"value": "mudancas frete", "label": "Mudancas / Frete"},
            {"value": "pintura residencial pintor", "label": "Pintura Residencial"},
        ],
    },
    "outros": {
        "label": "Outros",
        "types": [
            {"value": "casa de festas eventos", "label": "Casa de Festas"},
            {"value": "empresa seguranca vigilancia", "label": "Empresa de Seguranca"},
            {"value": "funeraria servicos funerarios", "label": "Funeraria"},
            {"value": "hotel pousada hospedagem", "label": "Hotel / Pousada"},
            {"value": "igreja templo religioso", "label": "Igreja / Templo"},
            {"value": "loterica", "label": "Loterica"},
            {"value": "studio tatuagem tattoo", "label": "Estudio de Tatuagem"},
            {"value": "transportadora logistica", "label": "Transportadora / Logistica"},
        ],
    },
}

BRAZILIAN_STATES = [
    {"uf": "AC", "name": "Acre"}, {"uf": "AL", "name": "Alagoas"},
    {"uf": "AP", "name": "Amapa"}, {"uf": "AM", "name": "Amazonas"},
    {"uf": "BA", "name": "Bahia"}, {"uf": "CE", "name": "Ceara"},
    {"uf": "DF", "name": "Distrito Federal"}, {"uf": "ES", "name": "Espirito Santo"},
    {"uf": "GO", "name": "Goias"}, {"uf": "MA", "name": "Maranhao"},
    {"uf": "MT", "name": "Mato Grosso"}, {"uf": "MS", "name": "Mato Grosso do Sul"},
    {"uf": "MG", "name": "Minas Gerais"}, {"uf": "PA", "name": "Para"},
    {"uf": "PB", "name": "Paraiba"}, {"uf": "PR", "name": "Parana"},
    {"uf": "PE", "name": "Pernambuco"}, {"uf": "PI", "name": "Piaui"},
    {"uf": "RJ", "name": "Rio de Janeiro"}, {"uf": "RN", "name": "Rio Grande do Norte"},
    {"uf": "RS", "name": "Rio Grande do Sul"}, {"uf": "RO", "name": "Rondonia"},
    {"uf": "RR", "name": "Roraima"}, {"uf": "SC", "name": "Santa Catarina"},
    {"uf": "SP", "name": "Sao Paulo"}, {"uf": "SE", "name": "Sergipe"},
    {"uf": "TO", "name": "Tocantins"},
]

PERSON_PREFIXES = [
    "dr.", "dra.", "dr ", "dra ", "prof.", "prof ", "profa", "me.", "adv.",
    "eng.", "arq.", "psic.", "fis.", "nut.", "farm.", "enf.",
]

BUSINESS_KEYWORDS = [
    "clinica", "centro", "hospital", "instituto", "loja", "restaurante",
    "academia", "escola", "escritorio", "agencia", "farmacia",
    "mercado", "supermercado", "padaria", "pet shop", "hotel", "pousada",
    "bar ", "buffet", "studio", "estudio", "salao", "spa ",
    "laboratorio", "oficina", "lava", "auto ", "concessionaria",
    "imobiliaria", "construtora", "distribuidora", "transportadora",
    "papelaria", "livraria", "grafica", "cartorio", "consultorio",
    "cia", "ltda", "eireli", "mei", "s/a", "ass.",
]


def is_person_profile(name: str) -> bool:
    """Detecta se 'name' parece nome de pessoa (Dr. Fulano) vs empresa.
    Portado da logica TS do Call Center."""
    if not name:
        return False
    lower = name.lower().strip()
    # Prefixos como Dr., Dra., Prof.
    for prefix in PERSON_PREFIXES:
        if lower.startswith(prefix):
            return True
    # Se tem 2-4 palavras E nao contem keyword de empresa, capitalizado em todas
    words = [w for w in lower.split() if w]
    if 2 <= len(words) <= 4:
        has_kw = any(kw in lower for kw in BUSINESS_KEYWORDS)
        if not has_kw:
            original = name.strip().split()
            all_capitalized = all(
                w and w[0].isupper() for w in original
            )
            if all_capitalized and all(len(w) > 1 for w in words):
                return True
    return False


def label_for_value(business_type_value: str):
    """Retorna (segment_key, label) para um value (ex: 'clinica estetica')."""
    for seg_key, seg in BUSINESS_SEGMENTS.items():
        for t in seg["types"]:
            if t["value"] == business_type_value:
                return seg_key, t["label"]
    return None, business_type_value

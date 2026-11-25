import math
import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ---------------------------------------------------------------
# 1) BANCO DE DADOS
# (Se vocês já têm os models prontos, apaguem esta parte
#  e importem os de vocês. Só garantam que os campos existam.)
# ATENÇÃO: o banco mudou. Apague o arquivo ygapo.db antes de rodar.
# ---------------------------------------------------------------
engine = create_engine("sqlite:///./ygapo.db", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


def agora():
    """Hora atual em UTC (sem fuso), usada para salvar no banco."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True)
    nome = Column(String)
    xp = Column(Integer, default=0)
    nivel = Column(Integer, default=1)


class Ecoponto(Base):
    __tablename__ = "ecopontos"
    id = Column(Integer, primary_key=True)
    nome = Column(String)
    latitude = Column(Float)
    longitude = Column(Float)
    qr_code_token = Column(String, unique=True)


class CarteiraRecurso(Base):
    __tablename__ = "carteiras"
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"))
    # recursos do jogo (RPG 3D)
    polimero_estelar = Column(Integer, default=0)
    liga_magnetica = Column(Integer, default=0)
    pergaminho_ancestral = Column(Integer, default=0)
    cristal_energia = Column(Integer, default=0)
    # moeda do app para cupons de parceiros
    eco_moedas = Column(Integer, default=0)


class HistoricoDescarte(Base):
    __tablename__ = "historico"
    id = Column(Integer, primary_key=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"))
    ecoponto_id = Column(Integer, ForeignKey("ecopontos.id"))
    material = Column(String)
    recurso = Column(String)
    xp_ganho = Column(Integer)
    eco_moedas_ganhas = Column(Integer)
    confianca = Column(Float)
    criado_em = Column(DateTime, default=agora)


Base.metadata.create_all(engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------------------------------------------------------------
# 2) REGRAS DO JOGO (fácil de mudar)
# material -> (coluna da carteira, nome do recurso, XP, EcoMoedas)
# Os nomes dos materiais são as classes do modelo YOLO do projeto.
# ---------------------------------------------------------------
REGRAS = {
    "pet_bottle":      ("polimero_estelar",     "Polímero Estelar",      10, 10),
    "aluminum_can":    ("liga_magnetica",       "Liga Magnética",        10, 10),
    "paper_cardboard": ("pergaminho_ancestral", "Pergaminho Ancestral",   5,  5),
    "glass_bottle":    ("cristal_energia",      "Cristal de Energia",     8,  8),
}

RAIO_MAXIMO_METROS = 30        # geofencing (PDF: 15 a 30 m)
CONFIANCA_MINIMA = 0.75        # abaixo disso, pede para tentar de novo
LIMITE_DIARIO = 5              # descartes bonificados por dia
INTERVALO_MESMO_PONTO_SEG = 120  # 2 minutos entre descartes no mesmo ponto
VALIDADE_TIMESTAMP_SEG = 60    # requisição muito antiga é recusada
FUSO_MANAUS = timezone(timedelta(hours=-4))

# MODO DEMONSTRAÇÃO: só liga se você iniciar a API com YGAPO_DEMO=1.
# Desliga geofencing, limite diário, intervalo entre descartes e validade do timestamp.
DEMO = os.getenv("YGAPO_DEMO") == "1"
if DEMO:
    print("*** MODO DEMONSTRAÇÃO LIGADO: antifraude relaxado ***")


def calcular_nivel(xp: int) -> int:
    return xp // 100 + 1  # a cada 100 XP sobe 1 nível


def distancia_metros(lat1, lon1, lat2, lon2) -> float:
    """Distância entre dois pontos do GPS (fórmula de Haversine)."""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def inicio_do_dia_utc() -> datetime:
    """Meia-noite de hoje em Manaus, convertida para UTC (como o banco guarda)."""
    meia_noite = datetime.now(FUSO_MANAUS).replace(hour=0, minute=0, second=0, microsecond=0)
    return meia_noite.astimezone(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------
# 3) AS DUAS ROTAS
# ---------------------------------------------------------------
app = FastAPI(title="Ygapó API")


@app.get("/api/ecoponto/{qr_code_token}")
def validar_qr_code(qr_code_token: str, db: Session = Depends(get_db)):
    ecoponto = db.query(Ecoponto).filter_by(qr_code_token=qr_code_token).first()
    if not ecoponto:
        raise HTTPException(404, "QR Code não pertence a nenhum ecoponto")
    return {
        "id": ecoponto.id,
        "nome": ecoponto.nome,
        "latitude": ecoponto.latitude,
        "longitude": ecoponto.longitude,
    }


class DescarteIn(BaseModel):
    usuario_id: int
    ecoponto_id: int
    material_detectado: str   # ex: "pet_bottle"
    confianca: float          # 0 a 1, vem da IA do celular
    latitude: float           # GPS do jogador
    longitude: float
    timestamp: int            # segundos desde 1970 (time.time() no celular)
    mock_location: bool = False  # o app informa se o GPS falso está ativo


@app.post("/api/descarte/validar")
def validar_descarte(dados: DescarteIn, db: Session = Depends(get_db)):
    usuario = db.get(Usuario, dados.usuario_id)
    ecoponto = db.get(Ecoponto, dados.ecoponto_id)
    if not usuario or not ecoponto:
        raise HTTPException(404, "Usuário ou ecoponto não encontrado")

    if dados.material_detectado not in REGRAS:
        raise HTTPException(422, "Material não reconhecido")
    coluna, nome_recurso, xp_ganho, moedas = REGRAS[dados.material_detectado]

    # --- ANTIFRAUDE (na ordem do funil do PDF) ---
    if dados.mock_location:
        raise HTTPException(403, "Localização falsa detectada. Desative o GPS simulado.")

    if not DEMO and abs(time.time() - dados.timestamp) > VALIDADE_TIMESTAMP_SEG:
        raise HTTPException(400, "Requisição expirada. Tente novamente.")

    dist = distancia_metros(dados.latitude, dados.longitude, ecoponto.latitude, ecoponto.longitude)
    if not DEMO and dist > RAIO_MAXIMO_METROS:
        raise HTTPException(403, f"Você está a {dist:.0f} m do ecoponto. Chegue mais perto.")

    if dados.confianca < CONFIANCA_MINIMA:
        raise HTTPException(422, "O scanner não identificou o material. Tente aproximar da luz.")

    hoje = db.query(HistoricoDescarte).filter(
        HistoricoDescarte.usuario_id == usuario.id,
        HistoricoDescarte.criado_em >= inicio_do_dia_utc(),
    ).count()
    if not DEMO and hoje >= LIMITE_DIARIO:
        raise HTTPException(429, f"Limite de {LIMITE_DIARIO} descartes bonificados por dia atingido.")

    ultimo = db.query(HistoricoDescarte).filter_by(
        usuario_id=usuario.id, ecoponto_id=ecoponto.id
    ).order_by(HistoricoDescarte.criado_em.desc()).first()
    if not DEMO and ultimo and agora() - ultimo.criado_em < timedelta(seconds=INTERVALO_MESMO_PONTO_SEG):
        raise HTTPException(429, "Aguarde 2 minutos para descartar de novo neste ponto.")

    # --- CARTEIRA ---
    carteira = db.query(CarteiraRecurso).filter_by(usuario_id=usuario.id).first()
    if not carteira:
        carteira = CarteiraRecurso(usuario_id=usuario.id)
        db.add(carteira)
        db.flush()

    setattr(carteira, coluna, (getattr(carteira, coluna) or 0) + 1)
    carteira.eco_moedas = (carteira.eco_moedas or 0) + moedas

    # --- XP E NÍVEL ---
    nivel_antes = usuario.nivel
    usuario.xp += xp_ganho
    usuario.nivel = calcular_nivel(usuario.xp)

    # --- HISTÓRICO ---
    db.add(HistoricoDescarte(
        usuario_id=usuario.id, ecoponto_id=ecoponto.id,
        material=dados.material_detectado, recurso=nome_recurso,
        xp_ganho=xp_ganho, eco_moedas_ganhas=moedas, confianca=dados.confianca,
    ))
    db.commit()

    return {
        "recurso_gerado": nome_recurso,
        "eco_moedas_ganhas": moedas,
        "xp_ganho": xp_ganho,
        "xp_total": usuario.xp,
        "nivel": usuario.nivel,
        "subiu_de_nivel": usuario.nivel > nivel_antes,
        "descartes_restantes_hoje": max(LIMITE_DIARIO - hoje - 1, 0),
    }


# ---------------------------------------------------------------
# 4) DADOS DE TESTE (só se o banco estiver vazio)
# ---------------------------------------------------------------
with SessionLocal() as db:
    if not db.query(Usuario).first():
        db.add(Usuario(nome="Aluno Teste"))
        db.add(Ecoponto(nome="Ecoponto Praça da Matriz", latitude=-3.1363,
                        longitude=-60.0230, qr_code_token="YGAPO-001"))
        db.commit()
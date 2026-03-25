import streamlit as st
import pandas as pd
import requests
import base64
import os
import tempfile
import json
import re
import difflib
from fpdf import FPDF
from datetime import datetime
from io import BytesIO
import smtplib
from email.message import EmailMessage
from PIL import Image
from decimal import Decimal, ROUND_HALF_UP
import google.generativeai as genai
from pypdf import PdfWriter, PdfReader

# =========================================================
# --- FUNZIONE DI ARROTONDAMENTO COMMERCIALE ---
# =========================================================
def arrotonda(valore):
    """
    Arrotonda il valore al secondo decimale per eccesso commerciale.
    Es. 23.555 -> 23.56
    """
    return float(Decimal(str(valore)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

# =========================================================
# --- RICERCA INTELLIGENTE SCHEDE TECNICHE ---
# =========================================================
@st.cache_data
def get_tutte_le_schede(cartella="schede"):
    """Recupera la lista di tutti i file PDF nella cartella schede"""
    if not os.path.exists(cartella): return []
    return sorted([f for f in os.listdir(cartella) if f.lower().endswith('.pdf')])

def trova_scheda_migliore(codice_articolo, lista_file):
    """Cerca il file PDF più probabile in base al codice."""
    codice_pulito = codice_articolo.replace("⚠️", "").strip().upper()
    
    # 1. Prova a vedere se il codice è contenuto nel nome (es. B0134 in GS_CA-B0134...)
    for file_name in lista_file:
        if codice_pulito in file_name.upper():
            return file_name
    
    # 2. Se non lo trova, prova a cercare solo i primi 5 caratteri
    if len(codice_pulito) >= 5:
        prefisso = codice_pulito[:5]
        for file_name in lista_file:
            if prefisso in file_name.upper():
                return file_name
            
    return None

# =========================================================
# --- CONFIGURAZIONE EMAIL PER ONEDRIVE/DRIVE ---
# =========================================================
EMAIL_MITTENTE = "agentecavallo@gmail.com"  
PASSWORD_APP = "ciqnxbsqttnchoyo"            
EMAIL_DESTINATARIO = "agentecavallo@gmail.com" 

def invia_pdf_via_email(pdf_bytes, nome_file):
    msg = EmailMessage()
    msg['Subject'] = f"Nuovo Preventivo: {nome_file}"
    msg['From'] = EMAIL_MITTENTE
    msg['To'] = EMAIL_DESTINATARIO
    msg.set_content("In allegato il nuovo preventivo/ordine generato dall'app. Power Automate lo salverà su OneDrive/Drive.")
    msg.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename=nome_file)

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(EMAIL_MITTENTE, PASSWORD_APP)
            server.send_message(msg)
        return True, "✅ Inviato con successo a OneDrive (via Email)!"
    except Exception as e:
        return False, f"⚠️ Errore durante l'invio: {e}"

# =========================================================
# --- GESTIONE CLOUD JSONBIN PER I PREVENTIVI ---
# =========================================================
def carica_storico_cloud():
    if "JSONBIN_API_KEY" not in st.secrets or "JSONBIN_BIN_ID" not in st.secrets:
        return {}
    try:
        url = f"https://api.jsonbin.io/v3/b/{st.secrets['JSONBIN_BIN_ID']}/latest"
        headers = {'X-Master-Key': st.secrets['JSONBIN_API_KEY']}
        risposta = requests.get(url, headers=headers)
        if risposta.status_code == 200:
            return risposta.json().get('record', {})
        return {}
    except Exception:
        return {}

def salva_preventivo_cloud(cliente, referente, note, carrello, pag, trasp, val, sc_base, sc_atg):
    if not cliente: return False, "⚠️ Inserisci almeno il Nome Cliente."
    if not carrello: return False, "⚠️ Il carrello è vuoto."
    if "JSONBIN_API_KEY" not in st.secrets: return False, "⚠️ Chiavi API mancanti."

    storico = carica_storico_cloud()
    ora_attuale = datetime.now()
    
    id_univoco = None
    
    for key in list(storico.keys()):
        if key.startswith(f"{cliente} - "):
            date_str = key.replace(f"{cliente} - ", "")
            try:
                dt_obj = datetime.strptime(date_str, "%d.%m.%Y %H:%M")
                if (ora_attuale - dt_obj).total_seconds() <= 3600:
                    id_univoco = key
                    break
            except ValueError:
                pass

    if not id_univoco:
        data_ora = ora_attuale.strftime("%d.%m.%Y %H:%M")
        id_univoco = f"{cliente} - {data_ora}"
        data_salvataggio = data_ora
    else:
        data_salvataggio = storico[id_univoco]["data_salvataggio"]

    storico[id_univoco] = {
        "data_salvataggio": data_salvataggio,
        "cliente": cliente,
        "referente": referente,
        "note": note,
        "pagamento": pag,
        "trasporto": trasp,
        "validita": val,
        "sconti_base": sc_base,
        "sconti_atg": sc_atg,
        "carrello": carrello
    }

    try:
        url = f"https://api.jsonbin.io/v3/b/{st.secrets['JSONBIN_BIN_ID']}"
        headers = {'Content-Type': 'application/json', 'X-Master-Key': st.secrets['JSONBIN_API_KEY']}
        risposta = requests.put(url, json=storico, headers=headers)
        if risposta.status_code == 200:
            return True, "✅ Preventivo salvato/aggiornato in Archivio!"
        return False, f"⚠️ Errore API: {risposta.status_code}"
    except Exception as e:
        return False, f"⚠️ Errore di connessione: {e}"

def elimina_preventivo_cloud(id_univoco):
    if "JSONBIN_API_KEY" not in st.secrets: return False, "⚠️ Chiavi API mancanti."
    storico = carica_storico_cloud()
    if id_univoco in storico:
        del storico[id_univoco]  
        try:
            url = f"https://api.jsonbin.io/v3/b/{st.secrets['JSONBIN_BIN_ID']}"
            headers = {'Content-Type': 'application/json', 'X-Master-Key': st.secrets['JSONBIN_API_KEY']}
            risposta = requests.put(url, json=storico, headers=headers)
            if risposta.status_code == 200:
                return True, f"✅ Preventivo eliminato!"
            return False, f"⚠️ Errore API: {risposta.status_code}"
        except Exception as e:
            return False, f"⚠️ Errore di connessione: {e}"
    return False, "⚠️ Preventivo non trovato."

# =========================================================
# --- INIZIALIZZAZIONE APP E DATI ---
# =========================================================
st.set_page_config(page_title="Generatore Preventivi", layout="wide", page_icon="📄")

st.markdown("""
<style>
div[data-testid="stTextInput"] input { background-color: #e8f5e9 !important; border: 2px solid #4CAF50 !important; color: #000000 !important; font-weight: bold; }
button[kind="primary"] { background-color: #4CAF50 !important; color: white !important; border: none !important; }
button[kind="primary"]:hover { background-color: #45a049 !important; }
</style>
""", unsafe_allow_html=True)

if 'carrello' not in st.session_state: st.session_state['carrello'] = []
if 'espositori_selezionati' not in st.session_state: st.session_state['espositori_selezionati'] = []
if 'schede_associate' not in st.session_state: st.session_state['schede_associate'] = {}

@st.cache_data
def carica_dati(path, tipo="base"):
    if not os.path.exists(path): return None
    try:
        data = pd.read_excel(path)
        if tipo == "atg":
            data = data.iloc[:, :6]
            data.columns = ['ARTICOLO', 'RIVESTIMENTO', 'QTA_BOX', 'RANGE_TAGLIE', 'LISTINO', 'IMMAGINE']
        else:
            nomi_colonne = [str(c).strip().upper() for c in data.columns]
            if len(nomi_colonne) > 5: nomi_colonne[5] = 'NORMATIVA' 
            data.columns = nomi_colonne
        return data
    except Exception as e:
        return None

df_base = carica_dati('Listino_agente.xlsx', "base")
df_atg = carica_dati('Listino_ATG.xlsx', "atg")

miei_headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
}

# =========================================================
# --- CALLBACKS ---
# =========================================================
def aggiorna_prezzi_automaticamente():
    if not st.session_state.get('carrello'): return
        
    sc_b1 = st.session_state.get('sc_base1', 40.0)
    sc_b2 = st.session_state.get('sc_base2', 10.0)
    sc_b3 = st.session_state.get('sc_base3', 0.0)
    sc_a1 = st.session_state.get('sc_atg1', 40.0)
    sc_a2 = st.session_state.get('sc_atg2', 10.0)
    sc_a3 = st.session_state.get('sc_atg3', 0.0)
    
    aggiornati = False
    for riga in st.session_state['carrello']:
        art = riga["Articolo"]
        listino = 0.0
        catalogo = ""
        
        if art.startswith("⚠️"):
            continue
            
        if df_base is not None and art in df_base['ARTICOLO'].values:
            listino = float(df_base[df_base['ARTICOLO'] == art].iloc[0]['LISTINO'])
            catalogo = "Listino Base"
        elif df_atg is not None and art in df_atg['ARTICOLO'].values:
            listino = float(df_atg[df_atg['ARTICOLO'] == art].iloc[0]['LISTINO'])
            catalogo = "Listino ATG"
        
        if catalogo == "Listino Base":
            molt = (1 - sc_b1/100) * (1 - sc_b2/100) * (1 - sc_b3/100)
        elif catalogo == "Listino ATG":
            molt = (1 - sc_a1/100) * (1 - sc_a2/100) * (1 - sc_a3/100)
        else:
            continue
            
        nuovo_netto = arrotonda(listino * molt)
        riga["Netto U."] = f"{nuovo_netto:.2f} €"
        riga["Totale Riga"] = arrotonda(nuovo_netto * riga["Quantità"])
        aggiornati = True
        
    if aggiornati:
        st.session_state['msg_successo'] = "🔄 Prezzi ricalcolati in base ai nuovi sconti!"

def esegui_azioni_finali(cliente, referente, note, carrello, pag, trasp, val, sc_base, sc_atg):
    if 'pdf_pronto' in st.session_state and 'nome_file_pronto' in st.session_state:
        succ_em, msg_em = invia_pdf_via_email(st.session_state['pdf_pronto'], st.session_state['nome_file_pronto'])
        st.session_state['esito_email'] = (succ_em, msg_em)
    succ_cl, msg_cl = salva_preventivo_cloud(cliente, referente, note, carrello, pag, trasp, val, sc_base, sc_atg)
    st.session_state['esito_cloud'] = (succ_cl, msg_cl)

def callback_salva_solo_cloud(cliente, referente, note, carrello, pag, trasp, val, sc_base, sc_atg):
    succ, msg = salva_preventivo_cloud(cliente, referente, note, carrello, pag, trasp, val, sc_base, sc_atg)
    if succ: st.session_state['msg_successo'] = msg
    else: st.session_state['msg_errore'] = msg

def esegui_caricamento(d):
    st.session_state['carrello'] = d.get('carrello', [])
    st.session_state['nome_cliente_input'] = d.get('cliente', '')
    st.session_state['nome_referente_input'] = d.get('referente', '')
    st.session_state['note_input'] = d.get('note', '')
    st.session_state['pagamento_input'] = d.get('pagamento', 'Solito in uso')
    st.session_state['trasporto_input'] = d.get('trasporto', 'P.to Franco 300,00')
    st.session_state['validita_input'] = d.get('validita', '30.06.2026')
    st.session_state['sc_base1'] = d.get('sconti_base', [40.0, 10.0, 0.0])[0]
    st.session_state['sc_base2'] = d.get('sconti_base', [40.0, 10.0, 0.0])[1]
    st.session_state['sc_base3'] = d.get('sconti_base', [40.0, 10.0, 0.0])[2]
    st.session_state['sc_atg1'] = d.get('sconti_atg', [40.0, 10.0, 0.0])[0]
    st.session_state['sc_atg2'] = d.get('sconti_atg', [40.0, 10.0, 0.0])[1]
    st.session_state['sc_atg3'] = d.get('sconti_atg', [40.0, 10.0, 0.0])[2]

def callback_aggiungi_taglie(articolo, img, normativa, prezzo, taglie, catalogo):
    aggiunti = False
    st.session_state['ultima_modalita'] = "Specifica Taglie"
    for t in taglie:
        key = f"qta_{t}_{catalogo}"
        q = st.session_state.get(key, 0)
        if q > 0:
            st.session_state['carrello'].append({
                "Articolo": articolo, "Taglia": t, "Quantità": q,
                "Netto U.": f"{arrotonda(prezzo):.2f} €", 
                "Totale Riga": arrotonda(prezzo * q),
                "Immagine": img, "Normativa": normativa
            })
            st.session_state[key] = 0  
            aggiunti = True
    if aggiunti: st.session_state['msg_successo'] = "Aggiunto!"

def callback_aggiungi_generico(articolo, img, normativa, prezzo):
    st.session_state['ultima_modalita'] = "Solo Modello/Vetrina"
    q = st.session_state.get('qta_generica_input', 0)
    st.session_state['carrello'].append({
        "Articolo": articolo, "Taglia": "-", "Quantità": q,
        "Netto U.": f"{arrotonda(prezzo):.2f} €", 
        "Totale Riga": arrotonda(prezzo * q),
        "Immagine": img, "Normativa": normativa
    })
    st.session_state['qta_generica_input'] = 0  
    st.session_state['msg_successo'] = "Aggiunto!"

def callback_svuota_tutto():
    st.session_state['carrello'] = []
    st.session_state.pop('schede_associate', None)
    for k in ['pdf_pronto', 'esito_cloud', 'esito_email']:
        st.session_state.pop(k, None)

def callback_elimina_riga(idx):
    if 0 <= idx < len(st.session_state['carrello']):
        st.session_state['carrello'].pop(idx)

# =========================================================
# --- SIDEBAR: DATI PRINCIPALI ---
# =========================================================
st.sidebar.header("📋 Dati Documento")
nome_cliente = st.sidebar.text_input("Nome del Cliente:", placeholder="Ragione Sociale...", key="nome_cliente_input")
nome_referente = st.sidebar.text_input("Nome Referente:", placeholder="Mario Rossi...", key="nome_referente_input")

st.sidebar.divider()
st.sidebar.header("💰 Sconto Base")
col_sc1, col_sc2, col_sc3 = st.sidebar.columns(3)
sc1 = col_sc1.number_input("Sc. 1 %", 0.0, 100.0, 40.0, key="sc_base1", on_change=aggiorna_prezzi_automaticamente)
sc2 = col_sc2.number_input("Sc. 2 %", 0.0, 100.0, 10.0, key="sc_base2", on_change=aggiorna_prezzi_automaticamente)
sc3 = col_sc3.number_input("Sc. 3 %", 0.0, 100.0, 0.0, key="sc_base3", on_change=aggiorna_prezzi_automaticamente)

st.sidebar.divider()
st.sidebar.header("🧤 Sconto ATG")
col_atg1, col_atg2, col_atg3 = st.sidebar.columns(3)
sc_atg1 = col_atg1.number_input("Sc. ATG 1 %", 0.0, 100.0, 40.0, key="sc_atg1", on_change=aggiorna_prezzi_automaticamente)
sc_atg2 = col_atg2.number_input("Sc. ATG 2 %", 0.0, 100.0, 10.0, key="sc_atg2", on_change=aggiorna_prezzi_automaticamente)
sc_atg3 = col_atg3.number_input("Sc. ATG 3 %", 0.0, 100.0, 0.0, key="sc_atg3", on_change=aggiorna_prezzi_automaticamente)

st.sidebar.divider()
st.sidebar.header("🎁 Espositori Omaggio")
col_esp1, col_esp2 = st.sidebar.columns(2)
col_esp3, col_esp4 = st.sidebar.columns(2)

def seleziona_espositore(nome_file_img):
    if nome_file_img not in st.session_state['espositori_selezionati']:
        st.session_state['espositori_selezionati'].append(nome_file_img)
        st.toast(f"Aggiunto: {nome_file_img.replace('.jpg','')}", icon="✅")

with col_esp1:
    if st.button("ATG Banco", use_container_width=True): seleziona_espositore("ATG banco.jpg")
with col_esp2:
    if st.button("ATG Terra", use_container_width=True): seleziona_espositore("ATG terra.jpg")
with col_esp3:
    if st.button("Base Banco", use_container_width=True): seleziona_espositore("Base banco.jpg")
with col_esp4:
    if st.button("Base Terra", use_container_width=True): seleziona_espositore("BASE terra.jpg")

if st.session_state['espositori_selezionati']:
    st.sidebar.markdown("**Espositori inclusi:**")
    for esp in st.session_state['espositori_selezionati']:
        st.sidebar.success(f"✅ {esp}")
    if st.sidebar.button("❌ Rimuovi Tutti"):
        st.session_state['espositori_selezionati'] = []
        st.rerun()

st.sidebar.divider()
st.sidebar.header("⚖️ Condizioni Commerciali")

opzioni_pagamento = ["Solito in uso", "Da concordare", "Ri.Ba. 60 giorni", "Ri.ba. 60/90 giorni"]
if 'pagamento_input' in st.session_state and st.session_state['pagamento_input'] not in opzioni_pagamento:
    opzioni_pagamento.append(st.session_state['pagamento_input'])

campo_pagamento = st.sidebar.selectbox("Pagamento:", options=opzioni_pagamento, key="pagamento_input")

opzioni_trasporto = [
    "P.to Franco 300,00",
    "P.to Franco 500,00 (per ordini B2B)",
    "P.to Franco 1000,00",
    "P.to franco 400,00 (sia Base che ATG)"
]
if 'trasporto_input' in st.session_state and st.session_state['trasporto_input'] not in opzioni_trasporto:
    opzioni_trasporto.append(st.session_state['trasporto_input'])

campo_trasporto = st.sidebar.selectbox("Trasporto:", options=opzioni_trasporto, key="trasporto_input")
campo_validita = st.sidebar.text_input("Validità Offerta:", value="30.06.2026", key="validita_input")

st.sidebar.divider()
note_preventivo = st.sidebar.text_area("📝 Note Aggiuntive:", height=200, key="note_input")

# NUOVO: Checkbox per nascondere/mostrare la firma
includi_firma = st.sidebar.checkbox("✍️ Includi la mia firma nel PDF", value=True, help="Deseleziona per creare un PDF anonimo senza i tuoi contatti (ideale per clienti B2B)")

st.sidebar.divider()
st.sidebar.header("📂 Archivio Preventivi")
storico = carica_storico_cloud()

if storico:
    ricerca_cliente = st.sidebar.text_input("🔍 Cerca cliente salvato:", placeholder="Digita per filtrare...")
    opzioni_preventivi = list(storico.keys())[::-1] 
    
    if ricerca_cliente:
        opzioni_preventivi = [p for p in opzioni_preventivi if ricerca_cliente.lower() in p.lower()]
    
    scelta_prev = st.sidebar.selectbox("Scegli un preventivo da gestire:", ["--- Seleziona ---"] + opzioni_preventivi)
    
    if scelta_prev != "--- Seleziona ---":
        col_carica, col_elimina = st.sidebar.columns([1, 1])
        
        with col_carica:
            st.button("⬇️ Carica", use_container_width=True, on_click=esegui_caricamento, args=(storico[scelta_prev],))
            
        with col_elimina:
            if st.button("❌ Elimina", use_container_width=True):
                st.session_state['conferma_eliminazione_id'] = scelta_prev
                st.rerun()
                
        # Blocco di conferma eliminazione
        if st.session_state.get('conferma_eliminazione_id') == scelta_prev:
            st.sidebar.warning("⚠️ Confermi l'eliminazione?")
            col_si, col_no = st.sidebar.columns(2)
            with col_si:
                if st.button("✔️ SÌ", use_container_width=True, type="primary"):
                    successo_elim, msg_elim = elimina_preventivo_cloud(scelta_prev)
                    st.session_state.pop('conferma_eliminazione_id', None)
                    if successo_elim:
                        st.sidebar.success(msg_elim)
                        st.rerun() 
                    else:
                        st.sidebar.error(msg_elim)
            with col_no:
                if st.button("❌ NO", use_container_width=True):
                    st.session_state.pop('conferma_eliminazione_id', None)
                    st.rerun()
else:
    st.sidebar.info("Nessun preventivo presente in archivio.")

# =========================================================
# --- MAIN ---
# =========================================================
michelone_logo = "michelone.jpg"
logo_html = ""
if os.path.exists(michelone_logo):
    with open(michelone_logo, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode()
    logo_html = f'<img src="data:image/jpeg;base64,{encoded_string}" style="width: 100px; border-radius: 8px; margin-left: 100px;">'

st.markdown(f'<div style="display: flex; align-items: center; margin-bottom: 20px;"><h1 style="margin: 0;">📄 OFFERTE & ORDINI</h1>{logo_html}</div>', unsafe_allow_html=True)

# GESTIONE MESSAGGI DI NOTIFICA
if 'msg_successo' in st.session_state: st.success(st.session_state.pop('msg_successo'))
if 'msg_errore' in st.session_state: st.error(st.session_state.pop('msg_errore'))
if 'msg_warning' in st.session_state: st.warning(st.session_state.pop('msg_warning'))

if df_base is None and df_atg is None:
    st.warning("⚠️ Nessun file Excel trovato.")
else:
    # --- SEZIONE: LETTURA INTELLIGENTE CON GEMINI ---
    with st.expander("📸 Inserimento Rapido (Intelligenza Artificiale)", expanded=False):
        if "GEMINI_API_KEY" in st.secrets:
            genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
            
            col_ai1, col_ai2 = st.columns(2)
            
            with col_ai1:
                tipo_analisi = st.radio(
                    "Cosa vuoi analizzare?",
                    ["📄 Testo (Lista scritta a mano o stampata)", "👟 Prodotti Reali (Foto di scarpe/guanti)"],
                    horizontal=False
                )
                
            with col_ai2:
                metodo_inserimento = st.radio(
                    "Come vuoi inserire l'immagine?",
                    ["📂 Carica File dal Dispositivo", "📷 Scatta una Foto Ora"],
                    horizontal=False
                )

            foto_caricata = None
            
            if metodo_inserimento == "📂 Carica File dal Dispositivo":
                foto_caricata = st.file_uploader("Seleziona l'immagine", type=['png', 'jpg', 'jpeg'])
            else:
                foto_caricata = st.camera_input("Inquadra e scatta la foto")
            
            if foto_caricata is not None:
                st.image(foto_caricata, width=200, caption="Anteprima immagine")
                if st.button("🧠 Estrai e Metti in Vetrina/Carrello", type="primary"):
                    with st.spinner("Sto analizzando l'immagine... dammi un secondo!"):
                        try:
                            img_pil = Image.open(foto_caricata)
                            
                            model = genai.GenerativeModel('gemini-3.1-flash-lite-preview') 
                            
                            if "Testo" in tipo_analisi:
                                prompt = """
                                Sei un assistente commerciale. Analizza questa immagine che contiene una lista di calzature/articoli.
                                Estrai il nome del modello e la quantità richiesta (se non c'è quantità, metti 0).
                                Devi rispondere ESCLUSIVAMENTE con un array JSON valido, senza altro testo.
                                Usa esattamente questa struttura:
                                [
                                  {"articolo": "NOME_MODELLO", "quantita": 10},
                                  {"articolo": "ALTRO_MODELLO", "quantita": 0}
                                ]
                                """
                            else:
                                prompt = """
                                Sei un esperto di calzature antinfortunistiche e guanti da lavoro (brand come Base Protection e ATG). 
                                Analizza questa fotografia che ritrae dei prodotti fisici reali.
                                Identifica il nome del modello esatto o quello più probabile per ogni articolo presente nell'immagine.
                                Conta quanti articoli dello stesso tipo sono visibili.
                                Devi rispondere ESCLUSIVAMENTE con un array JSON valido, senza altro testo.
                                Usa esattamente questa struttura:
                                [
                                  {"articolo": "NOME_MODELLO", "quantita": 2},
                                  {"articolo": "ALTRO_MODELLO", "quantita": 1}
                                ]
                                """
                            
                            risposta = model.generate_content([prompt, img_pil])
                            testo_generato = risposta.text
                            
                            match = re.search(r'\[.*\]', testo_generato, re.DOTALL)
                            if match:
                                testo_pulito = match.group(0)
                            else:
                                testo_pulito = testo_generato.replace("```json", "").replace("```", "").strip()
                                
                            articoli_estratti = json.loads(testo_pulito)
                            
                            # --- EASTER EGG: NESSUN ARTICOLO TROVATO ---
                            if len(articoli_estratti) == 0:
                                st.session_state['msg_warning'] = "🐜 Michele fai il serio e fai una foto fatta per bene, NUN SEMO QUI A CONTA' E FORMICHE!"
                                st.rerun()
                            
                            trovati = 0
                            articoli_non_trovati = []
                            
                            for item in articoli_estratti:
                                nome_art = str(item.get('articolo', '')).upper()
                                try:
                                    qta = int(item.get('quantita', 0))
                                except ValueError:
                                    qta = 0
                                
                                dati_articolo = None
                                catalogo_rif = ""
                                sconti = (0,0,0)
                                
                                match_base = pd.DataFrame()
                                match_atg = pd.DataFrame()
                                
                                if df_base is not None:
                                    match_base = df_base[df_base['ARTICOLO'].astype(str).str.upper().str.contains(nome_art, na=False, regex=False)]
                                
                                if match_base.empty and df_atg is not None:
                                    match_atg = df_atg[df_atg['ARTICOLO'].astype(str).str.upper().str.contains(nome_art, na=False, regex=False)]
                                
                                # --- RICERCA APPROSSIMATIVA (Fuzzy) ---
                                if match_base.empty and match_atg.empty:
                                    tutti_articoli = []
                                    selettore_base = []
                                    selettore_atg = []
                                    
                                    if df_base is not None:
                                        selettore_base = df_base['ARTICOLO'].astype(str).str.upper().tolist()
                                        tutti_articoli.extend(selettore_base)
                                    if df_atg is not None:
                                        selettore_atg = df_atg['ARTICOLO'].astype(str).str.upper().tolist()
                                        tutti_articoli.extend(selettore_atg)
                                    
                                    simili = difflib.get_close_matches(nome_art, tutti_articoli, n=1, cutoff=0.4)
                                    if simili:
                                        nome_art_simile = simili[0]
                                        if df_base is not None and nome_art_simile in selettore_base:
                                            match_base = df_base[df_base['ARTICOLO'].astype(str).str.upper() == nome_art_simile]
                                        if df_atg is not None and nome_art_simile in selettore_atg:
                                            match_atg = df_atg[df_atg['ARTICOLO'].astype(str).str.upper() == nome_art_simile]

                                if not match_base.empty:
                                    dati_articolo = match_base.iloc[0]
                                    catalogo_rif = "Listino Base"
                                    sconti = (sc1, sc2, sc3)
                                elif not match_atg.empty:
                                    dati_articolo = match_atg.iloc[0]
                                    catalogo_rif = "Listino ATG"
                                    sconti = (sc_atg1, sc_atg2, sc_atg3)
                                    
                                if dati_articolo is not None:
                                    listino = float(dati_articolo['LISTINO'])
                                    molt = (1 - sconti[0]/100) * (1 - sconti[1]/100) * (1 - sconti[2]/100)
                                    netto_calc = arrotonda(listino * molt)
                                    
                                    norm = str(dati_articolo.get('NORMATIVA', '')).strip() if catalogo_rif == "Listino Base" else ""
                                    if norm.lower() in ["nan", "none", "", "nat", "null"]: norm = ""
                                    
                                    st.session_state['carrello'].append({
                                        "Articolo": dati_articolo['ARTICOLO'], 
                                        "Taglia": "-", 
                                        "Quantità": qta,
                                        "Netto U.": f"{netto_calc:.2f} €", 
                                        "Totale Riga": arrotonda(netto_calc * qta),
                                        "Immagine": str(dati_articolo.get('IMMAGINE', '')).strip(), 
                                        "Normativa": norm
                                    })
                                    trovati += 1
                                else:
                                    st.session_state['carrello'].append({
                                        "Articolo": f"⚠️ {nome_art} (Verifica Manule)", 
                                        "Taglia": "-", 
                                        "Quantità": qta,
                                        "Netto U.": "0.00 €", 
                                        "Totale Riga": 0.0,
                                        "Immagine": "", 
                                        "Normativa": ""
                                    })
                                    articoli_non_trovati.append(nome_art)
                                    trovati += 1
                            
                            if trovati > 0:
                                st.session_state['msg_successo'] = f"✅ {trovati} articoli inseriti a carrello."
                                if articoli_non_trovati:
                                    lista_avvisi = "\n".join([f"- {art}" for art in articoli_non_trovati])
                                    st.session_state['msg_warning'] = f"⚠️ **Attenzione!** I seguenti articoli letti dall'IA non sono stati trovati a listino e sono stati aggiunti a **0,00 €**:\n{lista_avvisi}"
                                st.rerun() 
                                
                        except Exception as e:
                            st.error(f"Qualcosa è andato storto nella lettura dell'immagine: {e}")
        else:
            st.info("ℹ️ Per usare la lettura da foto, devi inserire `GEMINI_API_KEY = 'la-tua-chiave'` nei file `.streamlit/secrets.toml`.")

    st.divider()

    # --- SEZIONE: RICERCA MANUALE ---
    ricerca = st.text_input("🟢 Inserisci nome modello (Ricerca Manuale):", placeholder="Cerca su tutto il catalogo...").upper()

    if ricerca:
        risultati_trovati = []
        if df_base is not None:
            r_base = df_base[df_base['ARTICOLO'].astype(str).str.upper().str.contains(ricerca, na=False)].copy()
            if not r_base.empty:
                r_base['CATALOGO_PROVENIENZA'] = "Listino Base"
                risultati_trovati.append(r_base)
        if df_atg is not None:
            r_atg = df_atg[df_atg['ARTICOLO'].astype(str).str.upper().str.contains(ricerca, na=False)].copy()
            if not r_atg.empty:
                r_atg['CATALOGO_PROVENIENZA'] = "Listino ATG"
                risultati_trovati.append(r_atg)
        
        if risultati_trovati:
            risultato_completo = pd.concat(risultati_trovati, ignore_index=True)
            scelta = st.selectbox("Seleziona l'articolo:", risultato_completo['ARTICOLO'].unique())
            d = risultato_completo[risultato_completo['ARTICOLO'] == scelta].iloc[0]
            
            catalogo_selezionato = d['CATALOGO_PROVENIENZA']
            normativa_articolo = str(d.get('NORMATIVA', '')).strip() if catalogo_selezionato == "Listino Base" else ""
            if normativa_articolo.lower() in ["nan", "none", "", "nat", "null"]: normativa_articolo = ""
            
            sconto_applicato = (sc1, sc2, sc3) if catalogo_selezionato == "Listino Base" else (sc_atg1, sc_atg2, sc_atg3)
            taglie_disponibili = list(range(35, 51)) if catalogo_selezionato == "Listino Base" else [6, 7, 8, 9, 10, 11, 12]
            
            st.divider()
            c1, c2 = st.columns([2, 1])
            with c1:
                st.subheader(f"Modello: {d['ARTICOLO']}")
                st.caption(f"📍 Trovato in: **{catalogo_selezionato}**") 
                
                prezzo_listino = float(d['LISTINO'])
                st.markdown(f"🏷️ **Prezzo di Listino:** {prezzo_listino:.2f} €")
                
                if normativa_articolo:
                    st.markdown(f"🛡️ **Normativa:** {normativa_articolo}")
                
                moltiplicatore = (1 - sconto_applicato[0]/100) * (1 - sconto_applicato[1]/100) * (1 - sconto_applicato[2]/100)
                prezzo_netto_calcolato = arrotonda(prezzo_listino * moltiplicatore)
                
                col_p1, col_p2 = st.columns(2)
                with col_p1: st.markdown(f"### Prezzo Netto: :green[{prezzo_netto_calcolato:.2f} €]")
                with col_p2: prezzo_netto_manuale = st.number_input("Modifica Prezzo Netto (€):", min_value=0.0, value=None, step=0.10)
                
                prezzo_netto_finale = prezzo_netto_manuale if prezzo_netto_manuale and prezzo_netto_manuale > 0 else prezzo_netto_calcolato
                
                st.divider()
                
                opzioni_mod = ["Specifica Taglie", "Solo Modello/Vetrina"]
                idx_mod = opzioni_mod.index(st.session_state.get('ultima_modalita', "Solo Modello/Vetrina"))
                modalita = st.radio("Scegli la modalità:", opzioni_mod, index=idx_mod, horizontal=True)
                
                if modalita == "Specifica Taglie":
                    for row_start in range(0, len(taglie_disponibili), 8):
                        chunk = taglie_disponibili[row_start:row_start + 8]
                        cols = st.columns(8)
                        for j, t in enumerate(chunk):
                            with cols[j]:
                                key = f"qta_{t}_{catalogo_selezionato}"
                                if key not in st.session_state: st.session_state[key] = 0
                                st.number_input(str(t), min_value=0, step=1, key=key)

                    totale_paia_modello_corrente = sum(st.session_state.get(f"qta_{t}_{catalogo_selezionato}", 0) for t in taglie_disponibili)
                    if totale_paia_modello_corrente > 0:
                        st.info(f"🔢 Paia da aggiungere per il modello **{d['ARTICOLO']}**: **{totale_paia_modello_corrente}**")

                    img_url = str(d.get('IMMAGINE', '')).strip()
                    st.button(
                        "🛒 Aggiungi al Preventivo", 
                        use_container_width=True, 
                        type="primary", 
                        on_click=callback_aggiungi_taglie, 
                        args=(d['ARTICOLO'], img_url, normativa_articolo, prezzo_netto_finale, taglie_disponibili, catalogo_selezionato)
                    )
                else:
                    st.number_input("Quantità totale (paia):", min_value=0, step=1, key='qta_generica_input')
                    
                    q = st.session_state.get('qta_generica_input', 0)
                    if q == 0:
                        st.info(f"👁️ Verrà aggiunto come **Solo Vetrina/Proposta** (Quantità: 0)")
                    else:
                        st.info(f"🔢 Paia da aggiungere per il modello **{d['ARTICOLO']}**: **{q}**")
                        
                    img_url = str(d.get('IMMAGINE', '')).strip()
                    st.button(
                        "🛒 Aggiungi Modello", 
                        use_container_width=True, 
                        type="primary", 
                        on_click=callback_aggiungi_generico,
                        args=(d['ARTICOLO'], img_url, normativa_articolo, prezzo_netto_finale)
                    )
            with c2:
                url = str(d.get('IMMAGINE', '')).strip()
                if url.startswith('http'):
                    try:
                        r = requests.get(url, headers=miei_headers, timeout=5)
                        if r.status_code == 200: st.image(BytesIO(r.content), use_container_width=True)
                    except: pass

# =========================================================
# --- RIEPILOGO E PDF ---
# =========================================================
if st.session_state['carrello']:
    st.divider()
    st.header("🛒 Riepilogo")
    
    for index, riga in enumerate(st.session_state['carrello']):
        c1, c2, c3, c4, c5, c6 = st.columns([3, 1, 1, 2, 2, 1])
        c1.write(riga["Articolo"])
        c2.write(str(riga["Taglia"]))
        c3.write(str(riga["Quantità"]))
        c4.write(str(riga["Netto U."]))
        
        if isinstance(riga['Totale Riga'], (int, float)):
            c5.write(f"{riga['Totale Riga']:.2f} €")
        else:
            c5.write("0.00 €")
            
        c6.button("❌", key=f"del_{index}", on_click=callback_elimina_riga, args=(index,))
            
    totale_generale = arrotonda(sum(item["Totale Riga"] for item in st.session_state['carrello'] if isinstance(item["Totale Riga"], (int, float))))
    totale_paia_carrello = sum(item["Quantità"] for item in st.session_state['carrello'] if isinstance(item["Quantità"], int))
    
    col_totale1, col_totale2 = st.columns(2)
    with col_totale1: st.markdown(f"### Totale Generale: **{totale_generale:.2f} €**")
    with col_totale2: st.markdown(f"### Totale Paia: **{totale_paia_carrello}**")
    
    st.divider()
    
    # --- GESTIONE SCHEDE TECNICHE ---
    st.markdown("### 📎 Allegati: Schede Tecniche")
    allega_schede = st.checkbox("Sì, voglio allegare le schede tecniche in fondo al PDF finale", value=False, key="checkbox_schede")
    
    if allega_schede:
        st.info("💡 Qui sotto puoi vedere quale scheda verrà allegata per ogni modello. Se il sistema ha scelto quella sbagliata, cliccaci sopra per correggerla.")
        articoli_unici = list(set([r["Articolo"] for r in st.session_state['carrello'] if not str(r["Articolo"]).startswith("⚠️")]))
        lista_file_schede = get_tutte_le_schede("schede")
        opzioni_schede = ["--- Nessuna scheda ---"] + lista_file_schede
        
        if not lista_file_schede:
            st.warning("Nessun PDF trovato! Assicurati di aver creato la cartella 'schede' su GitHub e di averci messo dentro i file .pdf")
        else:
            for art in articoli_unici:
                # Se è la prima volta che vede questo articolo, prova ad abbinarlo in automatico
                if art not in st.session_state['schede_associate']:
                    match = trova_scheda_migliore(art, lista_file_schede)
                    st.session_state['schede_associate'][art] = match if match else "--- Nessuna scheda ---"
                
                # Trova l'indice del file nella tendina per mostrarlo come predefinito
                val_attuale = st.session_state['schede_associate'].get(art, "--- Nessuna scheda ---")
                try: 
                    idx = opzioni_schede.index(val_attuale)
                except ValueError: 
                    idx = 0
                
                # Crea la tendina
                scelta = st.selectbox(f"📝 Scheda per il modello **{art}**:", options=opzioni_schede, index=idx, key=f"sel_scheda_{art}")
                st.session_state['schede_associate'][art] = scelta

    st.divider()
    
    c_p1, c_p2, c_p3, c_p4 = st.columns(4)
    
    with c_p1:
        st.button("🗑️ Svuota Tutto", use_container_width=True, on_click=callback_svuota_tutto)
        
    with c_p2:
        st.button("🔄 Ricalcola Prezzi", use_container_width=True, on_click=aggiorna_prezzi_automaticamente)
        
    with c_p3:
        sconti_base = (sc1, sc2, sc3)
        sconti_atg = (sc_atg1, sc_atg2, sc_atg3)
        st.button(
            "💾 Salva Preventivo", 
            use_container_width=True, 
            on_click=callback_salva_solo_cloud,
            args=(nome_cliente, nome_referente, note_preventivo, st.session_state['carrello'], campo_pagamento, campo_trasporto, campo_validita, sconti_base, sconti_atg)
        )
            
    with c_p4:
        if st.button("📄 Prepara PDF", use_container_width=True, type="secondary"):
            st.session_state.pop('esito_cloud', None)
            st.session_state.pop('esito_email', None)
            
            with st.spinner("Generazione PDF in corso..."):
                raggruppo = {}
                for r in st.session_state['carrello']:
                    art = r["Articolo"]
                    if art not in raggruppo:
                        raggruppo[art] = {"T": [], "Tot": 0, "Img": r["Immagine"], "Netto": r["Netto U."], "Normativa": r.get("Normativa", "")}
                    if r["Quantità"] > 0:
                        if r["Taglia"] == "-": raggruppo[art]["T"].append(f"Q.tà: {r['Quantità']}pz")
                        else: raggruppo[art]["T"].append(f"Tg{r['Taglia']}: {r['Quantità']}pz")
                    
                    if isinstance(r["Totale Riga"], (int, float)):
                        raggruppo[art]["Tot"] += r["Totale Riga"]
                    raggruppo[art]["Tot"] = arrotonda(raggruppo[art]["Tot"])

                class PDF(FPDF):
                    def header(self):
                        for f in ["logo.png", "logo.jpg", "logo.jpeg"]:
                            if os.path.exists(f):
                                self.image(f, 5, 4, 70) 
                                break
                                
                        if self.page_no() == 1:
                            self.set_font("helvetica", "", 12)
                            self.set_xy(100, 15)
                            self.cell(100, 6, "Spett.le", align="R", ln=1)
                            self.set_font("helvetica", "B", 20) 
                            self.cell(0, 8, nome_cliente if nome_cliente else "Cliente", align="R", ln=1)
                            if nome_referente:
                                self.set_font("helvetica", "", 15) 
                                self.cell(0, 7, f"c.a. {nome_referente}", align="R", ln=1)
                            data_formattata = datetime.now().strftime("%d/%m/%Y")
                            self.set_font("helvetica", "I", 11)
                            self.cell(0, 7, f"Data: {data_formattata}", align="R", ln=1)
                        
                        self.set_y(60)

                pdf = PDF()
                pdf.add_page()
                
                for art, dati in raggruppo.items():
                    y_inizio = pdf.get_y()
                    if y_inizio > 230:
                        pdf.add_page()
                        y_inizio = pdf.get_y()

                    pdf.set_xy(10, y_inizio)
                    pdf.set_font("helvetica", "B", 12)
                    pdf.cell(110, 7, f"Modello: {art}", ln=1) 
                    if dati.get("Normativa"):
                        pdf.set_font("helvetica", "I", 9) 
                        pdf.cell(110, 5, f"Normativa: {dati['Normativa']}", ln=1) 
                    
                    pdf.set_font("helvetica", "", 10)
                    pdf.cell(110, 6, f"Prezzo Netto: {dati['Netto'].replace('€', 'Euro')}", ln=1) 
                    
                    pdf.set_font("helvetica", "I", 9)
                    if dati["T"]:
                        pdf.multi_cell(110, 5, " | ".join(dati["T"])) 
                    else:
                        pdf.cell(110, 5, "Proposta Modello", ln=1) 
                    
                    if dati['Tot'] > 0:
                        pdf.set_x(10)
                        pdf.set_font("helvetica", "B", 10)
                        pdf.cell(110, 6, f"Subtotale: {dati['Tot']:.2f} Euro", ln=1, align="L") 
                    
                    y_fine_testo = pdf.get_y()
                    y_fine_immagine = y_inizio + 10
                    
                    if dati["Img"] and dati["Img"].startswith("http"):
                        try:
                            res = requests.get(dati["Img"], headers=miei_headers, timeout=5)
                            if res.status_code == 200:
                                est = ".png" if ".png" in dati["Img"].lower() else ".jpg"
                                with tempfile.NamedTemporaryFile(delete=False, suffix=est) as tmp:
                                    tmp.write(res.content)
                                    tmp_name = tmp.name
                                
                                with Image.open(tmp_name) as img:
                                    w_px, h_px = img.size
                                    aspect_ratio = w_px / h_px
                                    max_w, max_h = 60.0, 52.5 
                                    
                                    if aspect_ratio > (max_w / max_h):
                                        final_w = max_w
                                        final_h = max_w / aspect_ratio
                                    else:
                                        final_h = max_h
                                        final_w = max_h * aspect_ratio
                                
                                    x_pos = 135 + (max_w - final_w) / 2
                                    y_pos = y_inizio + 2
                                    pdf.image(tmp_name, x=x_pos, y=y_pos, w=final_w, h=final_h)
                                    y_fine_immagine = y_pos + final_h + 2
                        except: pass
                    
                    pdf.set_y(max(y_fine_testo, y_fine_immagine) + 2)
                    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
                    pdf.ln(5)
                
                if totale_paia_carrello > 0:
                    pdf.ln(5)
                    pdf.set_font("helvetica", "B", 14)
                    pdf.cell(0, 10, f"TOTALE GENERALE: {totale_generale:.2f} Euro", ln=1, align="R")
                    pdf.set_font("helvetica", "I", 12)
                    pdf.cell(0, 6, f"(Totale Paia Complessive: {totale_paia_carrello})", ln=1, align="R")
                
                if 'espositori_selezionati' in st.session_state and st.session_state['espositori_selezionati']:
                    pdf.ln(10)
                    pdf.set_font("helvetica", "B", 14)
                    pdf.set_fill_color(240, 240, 240)
                    pdf.cell(0, 10, " ESPOSITORI OMAGGIO INCLUSI ", ln=1, align="C", fill=True)
                    pdf.ln(5)
                    
                    for esp in st.session_state['espositori_selezionati']:
                        y_inizio = pdf.get_y()
                        if y_inizio > 200: 
                            pdf.add_page()
                            y_inizio = pdf.get_y()
                            
                        nome_esp_pulito = esp.replace('.jpg', '').replace('.jpeg', '').replace('.png', '').upper()
                        pdf.set_font("helvetica", "B", 12)
                        pdf.cell(0, 8, nome_esp_pulito, ln=1, align="C")
                        
                        if os.path.exists(esp):
                            try:
                                with Image.open(esp) as img:
                                    w_px, h_px = img.size
                                    aspect_ratio = w_px / h_px
                                    max_w, max_h = 80.0, 80.0 
                                    
                                    if aspect_ratio > (max_w / max_h):
                                        final_w = max_w
                                        final_h = max_w / aspect_ratio
                                    else:
                                        final_h = max_h
                                        final_w = max_h * aspect_ratio
                                    
                                    x_pos = (210 - final_w) / 2 
                                    y_pos = pdf.get_y() + 2
                                    pdf.image(esp, x=x_pos, y=y_pos, w=final_w, h=final_h)
                                    
                                    pdf.set_y(y_pos + final_h + 8) 
                            except: 
                                pdf.ln(5)
                        else:
                            pdf.ln(5)
                        
                        pdf.line(10, pdf.get_y(), 200, pdf.get_y())
                        pdf.ln(5)

                pdf.ln(5)
                pdf.set_font("helvetica", "B", 10)
                pdf.cell(0, 6, "Condizioni Commerciali:", ln=1)
                pdf.set_font("helvetica", "", 10)
                pdf.cell(0, 6, f"Pagamento: {campo_pagamento}", ln=1)
                pdf.cell(0, 6, f"Trasporto: {campo_trasporto}", ln=1)
                pdf.cell(0, 6, f"Validità Offerta: {campo_validita}", ln=1)
                
                if note_preventivo:
                    pdf.ln(5)
                    pdf.set_font("helvetica", "B", 10)
                    pdf.cell(0, 6, "Note Aggiuntive:", ln=1)
                    pdf.set_font("helvetica", "", 10)
                    pdf.multi_cell(0, 6, note_preventivo)

                if includi_firma:
                    pdf.ln(15)
                    
                    y_corrente = pdf.get_y()
                    if y_corrente > 265:
                        pdf.add_page()
                        
                    pdf.set_font("helvetica", "B", 11)
                    pdf.cell(0, 5, "Michele Cavallo", ln=1, align="R")
                    pdf.set_font("helvetica", "", 10)
                    pdf.cell(0, 5, "Area Manager Base Protection srl", ln=1, align="R")
                    pdf.cell(0, 5, "Tel. 3890199088 Mail m.cavallo@baseprotection.com", ln=1, align="R")

                pdf.set_auto_page_break(auto=False)
                pdf.set_y(-20) 
                pdf.set_font("helvetica", "I", 7)
                pdf.set_text_color(128, 128, 128)
                disclaimer = "Il presente documento ha valore di proposta commerciale e non costituisce conferma d'ordine vincolante. Le condizioni, le quantità e i prezzi ivi riportati sono da intendersi validi salvo approvazione finale da parte della Direzione."
                pdf.multi_cell(0, 3, disclaimer, align="C")
                pdf.set_text_color(0, 0, 0)

                # --- UNIONE PDF SCHEDE TECNICHE ---
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    pdf.output(tmp_pdf.name)
                    preventivo_path = tmp_pdf.name

                writer = PdfWriter()
                
                reader_prev = PdfReader(preventivo_path)
                for page in reader_prev.pages:
                    writer.add_page(page)

                schede_aggiunte = 0
                
                # Se l'utente ha spuntato il box, incolla i file scelti nei menu a tendina
                if st.session_state.get("checkbox_schede", False):
                    articoli_unici = set([r["Articolo"] for r in st.session_state['carrello'] if not str(r["Articolo"]).startswith("⚠️")])
                    for art in articoli_unici:
                        nome_scheda = st.session_state['schede_associate'].get(art, "--- Nessuna scheda ---")
                        if nome_scheda != "--- Nessuna scheda ---":
                            percorso_scheda = os.path.join("schede", nome_scheda)
                            if os.path.exists(percorso_scheda):
                                try:
                                    reader_scheda = PdfReader(percorso_scheda)
                                    for page in reader_scheda.pages:
                                        writer.add_page(page)
                                    schede_aggiunte += 1
                                except Exception:
                                    pass

                output_finale = BytesIO()
                writer.write(output_finale)
                pdf_bytes = output_finale.getvalue()

                try: os.remove(preventivo_path)
                except: pass

                nome_sicuro = nome_cliente if nome_cliente else "Cliente"
                data_odierna = datetime.now().strftime("%d.%m.%Y")
                st.session_state['pdf_pronto'] = pdf_bytes
                st.session_state['nome_file_pronto'] = f"{nome_sicuro}_{data_odierna}.pdf"
                
                if schede_aggiunte > 0:
                    st.toast(f"✅ Ho allegato {schede_aggiunte} schede tecniche!", icon="📚")

    if 'pdf_pronto' in st.session_state:
        st.divider()
        st.success("✅ PDF Generato! Clicca il pulsante qui sotto per completare tutte le operazioni.")
        
        sconti_base = (sc1, sc2, sc3)
        sconti_atg = (sc_atg1, sc_atg2, sc_atg3)
        
        st.download_button(
            label="⬇️ Scarica PDF, Salva in Archivio e Invia Email 📧",
            data=st.session_state['pdf_pronto'],
            file_name=st.session_state['nome_file_pronto'],
            mime="application/pdf",
            use_container_width=True,
            type="primary",
            on_click=esegui_azioni_finali,
            args=(nome_cliente, nome_referente, note_preventivo, st.session_state['carrello'], campo_pagamento, campo_trasporto, campo_validita, sconti_base, sconti_atg)
        )
        
        if 'esito_cloud' in st.session_state:
            if st.session_state['esito_cloud'][0]: st.success(st.session_state['esito_cloud'][1])
            else: st.error(st.session_state['esito_cloud'][1])
            
        if 'esito_email' in st.session_state:
            if st.session_state['esito_email'][0]: st.success(st.session_state['esito_email'][1])
            else: st.error(st.session_state['esito_email'][1])

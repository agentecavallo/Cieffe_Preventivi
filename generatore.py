import streamlit as st
import pandas as pd
import requests
import base64
import os
import tempfile
import json
import pytz
from fpdf import FPDF
from datetime import datetime
from io import BytesIO
from PIL import Image
from decimal import Decimal, ROUND_HALF_UP

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
# --- GESTIONE ARCHIVIO LOCALE (SENZA API KEY) ---
# =========================================================
FILE_STORICO = "storico_preventivi.json"

def carica_storico():
    if not os.path.exists(FILE_STORICO):
        return {}
    try:
        with open(FILE_STORICO, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def salva_preventivo(cliente, referente, note, carrello, pag, trasp, val, sc_base, sc_atg):
    if not cliente: return False, "⚠️ Inserisci almeno il Nome Cliente."
    if not carrello: return False, "⚠️ Il carrello è vuoto."

    storico = carica_storico()
    # Imposto il fuso orario italiano
    fuso_italia = pytz.timezone('Europe/Rome')
    ora_attuale = datetime.now(fuso_italia)
    
    id_univoco = None
    
    for key in list(storico.keys()):
        if key.startswith(f"{cliente} - "):
            date_str = key.replace(f"{cliente} - ", "")
            try:
                dt_obj = datetime.strptime(date_str, "%d.%m.%Y %H:%M")
                # Faccio il check sui secondi ignorando il fuso per semplicità
                if (ora_attuale.replace(tzinfo=None) - dt_obj).total_seconds() <= 3600:
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
        with open(FILE_STORICO, "w", encoding="utf-8") as f:
            json.dump(storico, f, indent=4)
        return True, "✅ Preventivo salvato in Archivio!"
    except Exception as e:
        return False, f"⚠️ Errore di salvataggio: {e}"

def elimina_preventivo(id_univoco):
    storico = carica_storico()
    if id_univoco in storico:
        del storico[id_univoco]  
        try:
            with open(FILE_STORICO, "w", encoding="utf-8") as f:
                json.dump(storico, f, indent=4)
            return True, f"✅ Preventivo eliminato!"
        except Exception as e:
            return False, f"⚠️ Errore durante l'eliminazione: {e}"
    return False, "⚠️ Preventivo non trovato."

# =========================================================
# --- INIZIALIZZAZIONE APP E DATI ---
# =========================================================
st.set_page_config(page_title="Generatore Preventivi CIEFFE", layout="wide", page_icon="📄")

st.markdown("""
<style>
div[data-testid="stTextInput"] input { background-color: #e8f5e9 !important; border: 2px solid #4CAF50 !important; color: #000000 !important; font-weight: bold; }
button[kind="primary"] { background-color: #4CAF50 !important; color: white !important; border: none !important; }
button[kind="primary"]:hover { background-color: #45a049 !important; }
</style>
""", unsafe_allow_html=True)

if 'carrello' not in st.session_state: st.session_state['carrello'] = []

if 'pagamento_input' not in st.session_state: st.session_state['pagamento_input'] = "Solito in uso"
if 'trasporto_input' not in st.session_state: st.session_state['trasporto_input'] = "P.to Franco 300,00"
if 'validita_input' not in st.session_state: st.session_state['validita_input'] = "30.06.2026"

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
        
    sc_b1 = st.session_state.get('sc_base1', 0.0)
    sc_b2 = st.session_state.get('sc_base2', 0.0)
    sc_b3 = st.session_state.get('sc_base3', 0.0)
    sc_a1 = st.session_state.get('sc_atg1', 0.0)
    sc_a2 = st.session_state.get('sc_atg2', 0.0)
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
    succ_cl, msg_cl = salva_preventivo(cliente, referente, note, carrello, pag, trasp, val, sc_base, sc_atg)
    st.session_state['esito_cloud'] = (succ_cl, msg_cl)

def callback_salva_solo(cliente, referente, note, carrello, pag, trasp, val, sc_base, sc_atg):
    succ, msg = salva_preventivo(cliente, referente, note, carrello, pag, trasp, val, sc_base, sc_atg)
    if succ: st.session_state['msg_successo'] = msg
    else: st.session_state['msg_errore'] = msg

def esegui_caricamento(d):
    st.session_state['carrello'] = d.get('carrello', [])
    st.session_state['nome_cliente_input'] = d.get('cliente', '')
    st.session_state['nome_referente_input'] = d.get('referente', '')
    st.session_state['note_input'] = d.get('note', '')
    st.session_state['pagamento_input'] = d.get('pagamento', '')
    st.session_state['trasporto_input'] = d.get('trasporto', '')
    st.session_state['validita_input'] = d.get('validita', '30.06.2026')
    st.session_state['sc_base1'] = d.get('sconti_base', [0.0, 0.0, 0.0])[0]
    st.session_state['sc_base2'] = d.get('sconti_base', [0.0, 0.0, 0.0])[1]
    st.session_state['sc_base3'] = d.get('sconti_base', [0.0, 0.0, 0.0])[2]
    st.session_state['sc_atg1'] = d.get('sconti_atg', [0.0, 0.0, 0.0])[0]
    st.session_state['sc_atg2'] = d.get('sconti_atg', [0.0, 0.0, 0.0])[1]
    st.session_state['sc_atg3'] = d.get('sconti_atg', [0.0, 0.0, 0.0])[2]

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
    for k in ['pdf_pronto', 'esito_cloud']:
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
sc1 = col_sc1.number_input("Sc. 1 %", 0.0, 100.0, 0.0, key="sc_base1", on_change=aggiorna_prezzi_automaticamente)
sc2 = col_sc2.number_input("Sc. 2 %", 0.0, 100.0, 0.0, key="sc_base2", on_change=aggiorna_prezzi_automaticamente)
sc3 = col_sc3.number_input("Sc. 3 %", 0.0, 100.0, 0.0, key="sc_base3", on_change=aggiorna_prezzi_automaticamente)

st.sidebar.divider()
st.sidebar.header("🧤 Sconto ATG")
col_atg1, col_atg2, col_atg3 = st.sidebar.columns(3)
sc_atg1 = col_atg1.number_input("Sc. ATG 1 %", 0.0, 100.0, 0.0, key="sc_atg1", on_change=aggiorna_prezzi_automaticamente)
sc_atg2 = col_atg2.number_input("Sc. ATG 2 %", 0.0, 100.0, 0.0, key="sc_atg2", on_change=aggiorna_prezzi_automaticamente)
sc_atg3 = col_atg3.number_input("Sc. ATG 3 %", 0.0, 100.0, 0.0, key="sc_atg3", on_change=aggiorna_prezzi_automaticamente)

st.sidebar.divider()
st.sidebar.header("⚖️ Condizioni Commerciali")

campo_pagamento = st.sidebar.text_input("Pagamento:", key="pagamento_input")
campo_trasporto = st.sidebar.text_input("Trasporto:", key="trasporto_input")
campo_validita = st.sidebar.text_input("Validità Offerta:", key="validita_input")

st.sidebar.divider()
note_preventivo = st.sidebar.text_area("📝 Note Aggiuntive:", height=200, key="note_input")

st.sidebar.divider()
st.sidebar.header("📂 Archivio Preventivi")
storico = carica_storico()

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
                
        if st.session_state.get('conferma_eliminazione_id') == scelta_prev:
            st.sidebar.warning("⚠️ Confermi l'eliminazione?")
            col_si, col_no = st.sidebar.columns(2)
            with col_si:
                if st.button("✔️ SÌ", use_container_width=True, type="primary"):
                    successo_elim, msg_elim = elimina_preventivo(scelta_prev)
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
michelone_logo = "logo.png"
logo_html = ""
if os.path.exists(michelone_logo):
    with open(michelone_logo, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode()
    logo_html = f'<img src="data:image/png;base64,{encoded_string}" style="max-width: 300px; width: 100%; border-radius: 8px;">'
elif os.path.exists("logo.jpg"):
    with open("logo.jpg", "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode()
    logo_html = f'<img src="data:image/jpeg;base64,{encoded_string}" style="max-width: 300px; width: 100%; border-radius: 8px;">'

st.markdown(f'<div style="display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 20px;"><h1 style="margin: 0; min-width: 250px;">📄 OFFERTE & ORDINI</h1>{logo_html}</div>', unsafe_allow_html=True)

# GESTIONE MESSAGGI DI NOTIFICA
if 'msg_successo' in st.session_state: st.success(st.session_state.pop('msg_successo'))
if 'msg_errore' in st.session_state: st.error(st.session_state.pop('msg_errore'))
if 'msg_warning' in st.session_state: st.warning(st.session_state.pop('msg_warning'))

if df_base is None and df_atg is None:
    st.warning("⚠️ Nessun file Excel trovato.")
else:
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
            on_click=callback_salva_solo,
            args=(nome_cliente, nome_referente, note_preventivo, st.session_state['carrello'], campo_pagamento, campo_trasporto, campo_validita, sconti_base, sconti_atg)
        )
            
    with c_p4:
        if st.button("📄 Prepara PDF", use_container_width=True, type="secondary"):
            st.session_state.pop('esito_cloud', None)
            
            with st.spinner("Generazione PDF in corso..."):
                raggruppo = {}
                for r in st.session_state['carrello']:
                    art = r["Articolo"]
                    
                    label_prezzo = "Prezzo Netto:"
                            
                    if art not in raggruppo:
                        raggruppo[art] = {"T": [], "Tot": 0, "Img": r["Immagine"], "Netto": r["Netto U."], "Normativa": r.get("Normativa", ""), "Label": label_prezzo}
                    
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
                                try:
                                    with Image.open(f) as img:
                                        if img.mode == 'RGBA':
                                            background = Image.new('RGB', img.size, (255, 255, 255))
                                            background.paste(img, mask=img.split()[3]) 
                                            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_logo:
                                                background.save(tmp_logo.name, 'JPEG')
                                                logo_path = tmp_logo.name
                                            self.image(logo_path, 5, 4, 70) 
                                            os.remove(logo_path)
                                        else:
                                            self.image(f, 5, 4, 70) 
                                except Exception:
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
                            fuso_italia = pytz.timezone('Europe/Rome')
                            data_formattata = datetime.now(fuso_italia).strftime("%d.%m.%Y")
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
                    pdf.cell(110, 6, f"{dati['Label']} {dati['Netto'].replace('€', 'Euro')}", ln=1) 
                    
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

                pdf.ln(5)
                pdf.set_font("helvetica", "B", 10)
                pdf.cell(0, 6, "Condizioni Commerciali:", ln=1)
                pdf.set_font("helvetica", "", 10)
                pdf.cell(0, 6, f"Pagamento: {campo_pagamento}", ln=1)
                pdf.cell(0, 6, f"Trasporto: {campo_trasporto}", ln=1)
                pdf.cell(0, 6, f"Validità Offerta: {campo_validita}", ln=1)
                
                if note_preventivo:
                    pdf.ln(5)
                    pdf.set_font("helvetica", "B", 12)
                    pdf.cell(0, 6, "Note Aggiuntive:", ln=1)
                    pdf.set_font("helvetica", "", 11)
                    pdf.multi_cell(0, 6, note_preventivo)

                # --- FIRMA FISSA ---
                pdf.ln(15)
                y_corrente = pdf.get_y()
                if y_corrente > 265:
                    pdf.add_page()
                pdf.set_font("helvetica", "B", 11)
                pdf.cell(0, 5, "CIEFFE snc", ln=1, align="R")

                pdf.set_auto_page_break(auto=False)
                pdf.set_y(-20) 
                pdf.set_font("helvetica", "I", 7)
                pdf.set_text_color(128, 128, 128)
                disclaimer = "Il presente documento ha valore di proposta commerciale e non costituisce conferma d'ordine vincolante. Le condizioni, le quantità e i prezzi ivi riportati sono da intendersi validi salvo approvazione finale da parte della Direzione."
                pdf.multi_cell(0, 3, disclaimer, align="C")
                pdf.set_text_color(0, 0, 0)

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
                    pdf.output(tmp_pdf.name)
                    preventivo_path = tmp_pdf.name

                with open(preventivo_path, "rb") as f:
                    pdf_bytes = f.read()

                try: os.remove(preventivo_path)
                except: pass

                nome_sicuro = nome_cliente if nome_cliente else "Cliente"
                fuso_italia = pytz.timezone('Europe/Rome')
                data_odierna = datetime.now(fuso_italia).strftime("%d.%m.%Y")
                st.session_state['pdf_pronto'] = pdf_bytes
                st.session_state['nome_file_pronto'] = f"{nome_sicuro}_{data_odierna}.pdf"

    if 'pdf_pronto' in st.session_state:
        st.divider()
        st.success("✅ PDF Generato! Clicca il pulsante qui sotto per completare tutte le operazioni.")
        
        sconti_base = (sc1, sc2, sc3)
        sconti_atg = (sc_atg1, sc_atg2, sc_atg3)
        
        st.download_button(
            label="⬇️ Scarica PDF e Salva in Archivio 💾",
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

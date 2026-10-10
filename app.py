import streamlit as st
import os

st.title("Mon Tableau de Bord / Rapport HTML")

# Chemin vers votre fichier HTML généré quotidiennement
html_file_path = "output.html"

if os.path.exists(html_file_path):
    with open(html_file_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Option 1 : Afficher le HTML directement dans la page Streamlit
    st.components.v1.html(html_content, height=600, scrolling=True)

    # Option 2 : Proposer un lien de téléchargement direct
    st.download_button(
        label="Télécharger la page HTML",
        data=html_content,
        file_name="rapport.html",
        mime="text/html"
    )
else:
    st.info("Le script quotidien ne s'est pas encore exécuté ou aucun fichier HTML n'est disponible.")
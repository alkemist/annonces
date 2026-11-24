import sys
import json

import pandas as pd
from scrapper_leboncoin import ScrapperLeboncoin

codes = ["01400"]
searches = ['lapin']

scrapper = ScrapperLeboncoin("animal")
try:
    scrapper.login("jaden.achain+lbc@gmail.com", "Vivelesl@p1")
    conversations = scrapper.get_conversations()
finally:
    scrapper.close()

if len(conversations) > 0:
    df_conversations = pd.DataFrame(
        [{"id": conversation["id"], "annonce_id": conversation["annonce_id"],
          "author": conversation["author"], "is_free": conversation["is_free"],
          "has_certificat": conversation["has_certificat"], "need_answer": conversation["need_answer"],
          "wait_answer": conversation["wait_answer"]}
         for conversation in conversations]
    )
    df_conversations.to_csv("output/conversations.csv", index=False)

    messages = [
        {"conversation_id": conversation["id"], **message, "subjects": json.dumps(message["subjects"])}
        for conversation in conversations
        for message in conversation["messages"]
    ]
    df_messages = pd.DataFrame(
        messages, columns=["conversation_id", "sender", "content", "order", "type", "subjects"]
    )
    df_messages.to_csv("output/messages.csv", index=False)

# for code in codes:
#     output_file = f"output/lapins_{code}.csv"
#     annonces = []
#
#     annonces.extend(
#         scrapper.start({code: code}, searches)
#     )
#
#     if len(annonces) > 0:
#         df_annonces = pd.DataFrame(annonces) \
#             .sort_values(by=['site', 'date_update'], ascending=[True, False]) \
#             .drop_duplicates(subset=["url"], keep="first") \
#             .reset_index(drop=True)
#         df_annonces.to_csv(output_file, index=False)

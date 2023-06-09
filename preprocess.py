import json
import pickle
import os
from nltk import sent_tokenize
import itertools
import nltk

# Initialize the tokenizer
label_to_id = {}
relation_to_id = {}
json_directory = "test"
preprocessed_ner_data = []
preprocessed_re_data = []

from transformers import DistilBertTokenizerFast

max_seq_length = 128
tokenizer = DistilBertTokenizerFast.from_pretrained("distilbert-base-uncased", max_length=max_seq_length, padding="max_length")

def preprocess_data(json_data, label_to_id, relation_to_id):
    text = json_data["text"]

    # Find entities in the full text
    entities = json_data["entities"]
    entity_map = {}

    for entity in entities:
        entity_text = text[entity["span"]["begin"]:entity["span"]["end"]]
        entity_map[entity["entityId"]] = {
            "text": entity_text,
            "start": entity["span"]["begin"],
            "end": entity["span"]["end"],
        }

    # Find relations in the full text
    relations = json_data["relation_info"]
    
    ner_data = []
    re_data = []

    for relation in relations:
        subject_id = relation["subjectID"]
        object_id = relation["objectId"]
        rel_name = relation["rel_name"]
        
        if subject_id not in entity_map or object_id not in entity_map:
            print(f"Error: Entity IDs {subject_id} or {object_id} not found in the entity_map.")
            continue

        subject = entity_map[subject_id]["text"]
        subject_start = entity_map[subject_id]["start"]
        subject_end = entity_map[subject_id]["end"]

        obj = entity_map[object_id]["text"]
        object_start = entity_map[object_id]["start"]
        object_end = entity_map[object_id]["end"]
        
        if subject not in label_to_id:
            label = subject[:max_label_length]
            label_to_id[subject] = len(label_to_id)
        if obj not in label_to_id:
            label = obj[:max_label_length]
            label_to_id[obj] = len(label_to_id)
        if rel_name not in relation_to_id:
            relation_to_id[rel_name] = len(relation_to_id)

        # Find sentence containing the relation
        sentence_start = text.rfind(".", 0, subject_start) + 1
        sentence_end = text.find(".", object_end) + 1
        sentence_text = text[sentence_start:sentence_end].strip()

        # Tokenize the sentence
        sentence_encoding = tokenizer(
            sentence_text,
            return_offsets_mapping=True,
            padding='max_length',
            truncation=True,
            max_length=max_seq_length,
            return_tensors='pt'
        )
        sentence_tokens = tokenizer.convert_ids_to_tokens(sentence_encoding['input_ids'][0])
        sentence_token_offsets = sentence_encoding['offset_mapping'][0]

        # Find the entity token indices using the token offsets
        subject_start_idx, subject_end_idx, object_start_idx, object_end_idx = None, None, None, None
        subject_tokens = tokenizer.tokenize(subject)
        object_tokens = tokenizer.tokenize(obj)

        for i, (token_start, token_end) in enumerate(sentence_token_offsets):
            if token_start == subject_start - sentence_start:
                subject_start_idx = i
            if token_end == subject_end - sentence_start:
                subject_end_idx = i
            if token_start == object_start - sentence_start:
                object_start_idx = i
            if token_end == object_end - sentence_start:
                object_end_idx = i
                
        if len(sentence_tokens) > max_seq_length:
            print(f"Skipping item due to long tokenized sentence length: {len(sentence_tokens)}")
            continue

        # Check if the entity token indices are found
        if subject_start_idx is None or subject_end_idx is None or object_start_idx is None or object_end_idx is None:
            print(f"Skipping item due to missing entity token indices")
            print(f"Subject token indices: {subject_start_idx}-{subject_end_idx}")
            print(f"Object token indices: {object_start_idx}-{object_end_idx}\n")
            continue

        # Handle cases when token indices are not found
        if subject_start_idx is None or subject_end_idx is None:
            subject_start_idx, subject_end_idx = -1, -1
            for i, token in enumerate(sentence_tokens):
                if subject_tokens == sentence_tokens[i:i+len(subject_tokens)]:
                    subject_start_idx, subject_end_idx = i, i + len(subject_tokens) - 1
                    break

        if object_start_idx is None or object_end_idx is None:
            object_start_idx, object_end_idx = -1, -1
            for i, token in enumerate(sentence_tokens):
                if object_tokens == sentence_tokens[i:i+len(object_tokens)]:
                    object_start_idx, object_end_idx = i, i + len(object_tokens) - 1
                    break

        print(f"Subject: {subject}, Object: {obj}, Relation: {rel_name}")
        print(f"Sentence: {sentence_text}")
        print(f"Tokenized sentence: {sentence_tokens}")
        print(f"Subject token indices: {subject_start_idx}-{subject_end_idx}")
        print(f"Object token indices: {object_start_idx}-{object_end_idx}\n")
        re_data.append({
            "sentence_tokens": sentence_tokens,
            "subject_start_idx": subject_start_idx,
            "subject_end_idx": subject_end_idx,
            "object_start_idx": object_start_idx,
            "object_end_idx": object_end_idx,
            "rel_name": rel_name,
            "subject_text": subject,  # Add subject_text
            "object_text": obj,  # Add object_text
        })

        # Assuming you want to store the subject and object entities for ner_data
        ner_data.append({
            "subject_text": subject,
            "subject_start": subject_start,
            "subject_end": subject_end,
            "object_text": obj,
            "object_start": object_start,
            "object_end": object_end
        })
    return ner_data, re_data

max_label_length = 20
# Read JSON files
for file in os.listdir(json_directory):
    if file.endswith(".json"):
        with open(os.path.join(json_directory, file), "r") as json_file:
            json_data = json.load(json_file)
            ner_data, re_data = preprocess_data(json_data, label_to_id, relation_to_id)
            preprocessed_ner_data.extend(ner_data)
            preprocessed_re_data.extend(re_data)

# Save preprocessed data
with open("preprocessed_ner_data.pkl", "wb") as ner_file:
    pickle.dump(preprocessed_ner_data, ner_file)

with open("preprocessed_re_data.pkl", "wb") as re_file:
    pickle.dump(preprocessed_re_data, re_file)
with open("label_to_id.pkl", "wb") as f:
    pickle.dump(label_to_id, f)

with open("relation_to_id.pkl", "wb") as f:
    pickle.dump(relation_to_id, f)

print("Preprocessing completed.")

print("Preprocessing completed.")

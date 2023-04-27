import os
import torch
import json
from torch.utils.data import DataLoader, Dataset
from transformers import BertTokenizer, BertModel, BertPreTrainedModel, BertConfig, get_linear_schedule_with_warmup, AdamW
from tqdm import tqdm
from torch import nn
import warnings
import logging
import torch.nn as nn
import itertools
logging.getLogger("transformers").setLevel(logging.ERROR)

tokenizer = BertTokenizer.from_pretrained("bert-base-uncased")

batch_size = 8
num_epochs = 10
learning_rate = 5e-5

unique_ner_labels = set()
unique_relation_labels = set()
unique_ner_labels.add("O")

# Existing preprocessing functions
def preprocess_data(json_data, tokenizer, label_to_id, relation_to_id):
    ner_data = []
    re_data = []
    re_indices = []

    entities_dict = {entity["entityId"]: entity for entity in json_data["entities"]}

    # Build a set of entity IDs for faster lookup
    entity_ids = set(entities_dict.keys())

    # Build relation_dict
    relation_dict = {}
    for relation in json_data["relation_info"]:
        subject_id = relation["subjectID"].strip('"')  # Remove extra quotes
        obj_id = relation["objectId"].strip('"')  # Remove extra quotes
        if subject_id not in relation_dict:
            relation_dict[subject_id] = {}
        relation_dict[subject_id][obj_id] = relation["rel_name"]

    text = json_data["text"]
    current_idx = 0
    for entity in sorted(json_data["entities"], key=lambda x: x["span"]["begin"]):
        begin, end = entity["span"]["begin"], entity["span"]["end"]
        entity_type = entity["entityType"]
        entity_id = entity["entityId"].strip('"')
        entity_name = entity["entityName"]

        # Process NER data
        entity_text = text[begin:end]
        entity_tokens = tokenizer.tokenize(entity_text)

        while current_idx < begin:
            ner_data.append((text[current_idx], "O", len(ner_data)))
            current_idx += 1

        for i, token in enumerate(entity_tokens):
            if i == 0:
                label = f"B-{entity_type}-{entity_name}"
            else:
                label = f"I-{entity_type}-{entity_name}"

            # Add the label to the label_to_id dictionary if it's not present
            if label not in label_to_id:
                label_to_id[label] = len(label_to_id)

            ner_data.append((token, label, len(ner_data)))
            current_idx += 1

        current_idx = end

        # Add any new labels to the label_to_id mapping
        if f"{entity_type}-{entity_name}" not in label_to_id:
            label_to_id[f"{entity_type}-{entity_name}"] = len(label_to_id)

    # Process RE data
    for entity_id_1, entity_id_2 in itertools.combinations(entity_ids, 2):
        if entity_id_1 in relation_dict and entity_id_2 in relation_dict[entity_id_1]:
            # There is a relation between these entities
            rel_name = relation_dict[entity_id_1][entity_id_2]
            entity_1 = entities_dict[entity_id_1]
            entity_2 = entities_dict[entity_id_2]
            re_data.append({
                'id': (entity_id_1, entity_id_2),
                'subject': text[entity_1["span"]["begin"]:entity_1["span"]["end"]],
                'object': text[entity_2["span"]["begin"]:entity_2["span"]["end"]],
                'relation': rel_name,
                'subject_tokens': tokenizer.tokenize(text[entity_1["span"]["begin"]:entity_1["span"]["end"]]),
                'object_tokens': tokenizer.tokenize(text[entity_2["span"]["begin"]:entity_2["span"]["end"]])
            })

            # Add any new relations to the relation_to_id mapping
            if rel_name not in relation_to_id:
                relation_to_id[rel_name] = len(relation_to_id)

    while current_idx < len(text):
        ner_data.append((text[current_idx], "O", len(ner_data)))
        current_idx += 1

    if "O" not in label_to_id:
        label_to_id["O"] = len(label_to_id)

    # Return a list of dictionaries
    preprocessed_data = []
    
    preprocessed_data.append({
        'ner_data': ner_data,
        're_data': re_data,
        're_indices': re_indices
    })

    return preprocessed_data

class NERRE_Dataset(Dataset):
    def __init__(self, data, tokenizer, max_length):
        self.data = data
        self.tokenizer = tokenizer
        self.max_length = max_length

    def __len__(self):
        return len(self.data)

    def __getitem__(self, idx):
        item = self.data[idx]
        ner_data = item['ner_data']
        re_indices = item['re_indices']

        # Tokenize the text and prepare inputs
        tokens = [token for token, _, _ in ner_data]
        ner_labels = [label for _, label, _ in ner_data]
        inputs = self.tokenizer(tokens, padding='max_length', truncation=True, max_length=self.max_length, return_tensors='pt')

        input_ids = inputs['input_ids'].squeeze()
        attention_mask = inputs['attention_mask'].squeeze()
        token_type_ids = inputs['token_type_ids'].squeeze()

        return {
            'input_ids': input_ids,
            'attention_mask': attention_mask,
            'token_type_ids': token_type_ids,
            'ner_labels': torch.tensor(ner_labels, dtype=torch.long),
            're_labels': torch.tensor(item['re_labels'], dtype=torch.long),
            're_indices': torch.tensor(re_indices, dtype=torch.long)
        }


label_to_id = {}
relation_to_id = {}

json_directory = "test"
preprocessed_data = []

# Iterate through all JSON files in the directory
for file_name in os.listdir(json_directory):
    if file_name.endswith(".json"):
        json_path = os.path.join(json_directory, file_name)

        with open(json_path, "r") as json_file:
            json_data = json.load(json_file)

        preprocessed_file_data = preprocess_data(json_data, tokenizer, label_to_id, relation_to_id)
        preprocessed_data.extend(preprocessed_file_data)
        print(preprocessed_data)
        

max_length = 128
dataset = NERRE_Dataset(preprocessed_data, tokenizer, max_length)
dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)


class BertForNERAndRE(BertPreTrainedModel):
    def __init__(self, config, num_ner_labels, num_re_labels):
        super().__init__(config)
        self.bert = BertModel(config)
        self.dropout = nn.Dropout(config.hidden_dropout_prob)
        self.ner_classifier = nn.Linear(config.hidden_size, num_ner_labels)
        self.re_classifier = nn.Bilinear(config.hidden_size, config.hidden_size, num_re_labels)
        self.config.num_ner_labels = num_ner_labels
        self.config.num_re_labels = num_re_labels

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        ner_labels=None,
        re_labels=None,
        re_indices=None,
    ):
        outputs = self.bert(
            input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            position_ids=position_ids,
            head_mask=head_mask,
            inputs_embeds=inputs_embeds,
        )

        sequence_output, pooled_output = outputs.last_hidden_state, outputs.pooler_output

        sequence_output = self.dropout(sequence_output)
        pooled_output = self.dropout(pooled_output)

        ner_logits = self.ner_classifier(sequence_output)

        # Extract subject and object hidden states from sequence_output using re_indices
        subject_hidden_states = sequence_output[torch.arange(sequence_output.size(0)).unsqueeze(1), re_indices[:, 0]]
        object_hidden_states = sequence_output[torch.arange(sequence_output.size(0)).unsqueeze(1), re_indices[:, 1]]

        # Compute RE logits using the bilinear layer and the extracted hidden states
        re_logits = self.re_classifier(subject_hidden_states, object_hidden_states)

        total_loss = 0
        if ner_labels is not None:
            loss_fct = nn.CrossEntropyLoss()
            ner_loss = loss_fct(ner_logits.view(-1, self.config.num_ner_labels), ner_labels.view(-1))
            total_loss += ner_loss

        if re_labels is not None:
            loss_fct = nn.CrossEntropyLoss()
            re_loss = loss_fct(re_logits, re_labels.view(-1))
            total_loss += re_loss

        output_dict = {
            "loss": total_loss if total_loss > 0 else None,
            "ner_logits": ner_logits,
            "re_logits": re_logits
        }

        return output_dict


# Initialize the custom BERT model
from transformers import BertTokenizer, BertConfig, AdamW, get_linear_schedule_with_warmup

# Set up the configuration, model, and tokenizer
config = BertConfig.from_pretrained("bert-base-uncased")
tokenizer = BertTokenizer.from_pretrained("bert-base-uncased")

# Initialize the model with the given configuration
num_ner_labels = len(label_to_id)
num_re_labels = len(relation_to_id)
model = BertForNERAndRE(config, num_ner_labels, num_re_labels)

# Prepare the optimizer and learning rate scheduler
optimizer = AdamW(model.parameters(), lr=3e-5)
num_training_steps = len(dataloader) * num_epochs
scheduler = get_linear_schedule_with_warmup(optimizer, num_warmup_steps=0, num_training_steps=num_training_steps)

# Train the model using the dataloader
model.train()
for epoch in range(num_epochs):
    for batch in dataloader:
        input_ids = batch['input_ids']
        attention_mask = batch['attention_mask']
        token_type_ids = batch['token_type_ids']
        ner_labels = batch['ner_labels']
        re_labels = batch['re_labels']
        re_indices = batch['re_indices']

        # Forward pass
        outputs = model(
            input_ids=input_ids,
            attention_mask=attention_mask,
            token_type_ids=token_type_ids,
            ner_labels=ner_labels,
            re_labels=re_labels,
            re_indices=re_indices,
        )

        loss = outputs["loss"]
        loss.backward()

        # Update parameters and the learning rate
        optimizer.step()
        scheduler.step()
        optimizer.zero_grad()

# Save the fine-tuned custom BERT model and tokenizer
output_dir = "models/combined"
os.makedirs(output_dir, exist_ok=True)
model.save_pretrained(output_dir)
tokenizer.save_pretrained(output_dir)

# Save the label_to_id and relation_to_id mappings
with open(os.path.join(output_dir, "label_to_id.json"), "w") as f:
    json.dump(label_to_id, f)

with open(os.path.join(output_dir, "relation_to_id.json"), "w") as f:
    json.dump(relation_to_id, f)


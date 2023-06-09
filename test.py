import torch
import logging
import pickle
import os
from transformers import DistilBertConfig, DistilBertTokenizerFast
from DistiliBERT_train import DistilBertForNERAndRE  # Assuming the model is defined in a separate file called 'model.py'

logging.getLogger("transformers").setLevel(logging.ERROR)

output_dir = "models/combined"
with open(os.path.join(output_dir, "label_to_id.pkl"), "rb") as f:
    label_to_id = pickle.load(f)

with open(os.path.join(output_dir, "relation_to_id.pkl"), "rb") as f:
    relation_to_id = pickle.load(f)

id_to_label = {str(v): k for k, v in label_to_id.items()}
id_to_relation = {str(v): k for k, v in relation_to_id.items()}

config = DistilBertConfig.from_pretrained("distilbert-base-uncased")
tokenizer = DistilBertTokenizerFast.from_pretrained("distilbert-base-uncased")

num_ner_labels = len(label_to_id)
num_re_labels = len(relation_to_id)
model = DistilBertForNERAndRE(config, num_ner_labels, num_re_labels)

model_path = "models/combined/pytorch_model.bin"  # Replace with the path to your trained model
model.load_state_dict(torch.load(model_path, map_location=torch.device('cpu')))
model.eval()

# Move the model to the appropriate device (GPU if available, otherwise CPU)
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)
import nltk
from nltk.tokenize import sent_tokenize

def extract_entities_from_ner_labels(ner_labels, tokens, offsets):
    entities = []
    current_entity = None

    for i, (label, token) in enumerate(zip(ner_labels, tokens)):
        if label.startswith("B-"):
            if current_entity is not None:
                entities.append(current_entity)
            current_entity = {"text": token, "label": label[2:], "start": offsets[i][0], "end": offsets[i][1]}
        elif label.startswith("I-") and current_entity is not None:
            current_entity["text"] += " " + token
            current_entity["end"] = offsets[i][1]
        else:
            if current_entity is not None:
                entities.append(current_entity)
                current_entity = None

    if current_entity is not None:
        entities.append(current_entity)

    return entities


def generate_entity_pairs(entities):
    entity_pairs = []

    for i, entity1 in enumerate(entities):
        for j, entity2 in enumerate(entities):
            if i != j:
                entity_pairs.append((entity1["text"], entity2["text"]))

    return entity_pairs

def generate_re_data(sentence, entity_pairs, tokenizer):
    tokenized_sentences = tokenizer.tokenize(sentence)
    sentence_encoding = tokenizer(
        sentence,
        return_offsets_mapping=True,
        padding='max_length',
        truncation=True,
        max_length=128,
        return_tensors='pt'
    )
    sentence_tokens = tokenizer.convert_ids_to_tokens(sentence_encoding['input_ids'][0])
    offsets = sentence_encoding['offset_mapping'][0].tolist()

    re_data = []
    for entity_pair in entity_pairs:
        entity1, entity2 = entity_pair

        subject_start_idx, subject_end_idx = None, None
        object_start_idx, object_end_idx = None, None

        for i, (offset_start, offset_end) in enumerate(offsets):
            if subject_start_idx is None and offset_start == entity1["start"]:
                subject_start_idx = i
            if subject_end_idx is None and offset_end == entity1["end"]:
                subject_end_idx = i
            if object_start_idx is None and offset_start == entity2["start"]:
                object_start_idx = i
            if object_end_idx is None and offset_end == entity2["end"]:
                object_end_idx = i

            if subject_start_idx is not None and subject_end_idx is not None and object_start_idx is not None and object_end_idx is not None:
                break

        re_data.append({
            "subject_start_idx": subject_start_idx,
            "subject_end_idx": subject_end_idx,
            "object_start_idx": object_start_idx,
            "object_end_idx": object_end_idx
        })

    return re_data


def extract_relationships_large_text(text, model, tokenizer, id_to_label, id_to_relation):
    # Split the input text into sentences
    sentences = sent_tokenize(text)

    all_ner_labels = []
    all_re_labels = []

    # Process each sentence individually
    for sentence in sentences:
        inputs = tokenizer(sentence, return_tensors="pt", padding=True, truncation=True)

        # Move inputs to the same device as the model
        inputs = {k: v.to(model.device) for k, v in inputs.items()}

        # Run the model on the input text
        with torch.no_grad():
            outputs = model(**inputs)

        # Get the predicted NER and RE labels
        ner_predictions = torch.argmax(outputs["ner_logits"], dim=-1).squeeze().tolist()
        ner_labels = [id_to_label.get(str(pred), "unknown") for pred in ner_predictions]

        all_ner_labels.extend(ner_labels)


        # Extract entities from the NER layer output
        tokens = tokenizer.convert_ids_to_tokens(inputs["input_ids"].squeeze())
        entities = extract_entities_from_ner_labels(ner_labels, tokens, sentence_encoding['offset_mapping'][0].tolist())

        # Generate entity pairs
        entity_pairs = generate_entity_pairs(entities)

        # Generate re_data based on the entity pairs
        re_data = generate_re_data(sentence, entity_pairs, tokenizer)

        # Run the model again with re_data
        with torch.no_grad():
            outputs = model(**inputs, re_data=re_data)

        # Get the predicted RE labels
        re_predictions = torch.argmax(outputs["re_logits"], dim=-1).squeeze().tolist()
        re_labels = [[id_to_relation.get(str(pred), "no_relation") for pred in row] for row in re_predictions]

        all_re_labels.extend(re_labels)

    # Locate and print the subject and object entities along with their relationships
    for i, row in enumerate(all_re_labels):
        for j, relation in enumerate(row):
            if relation != "no_relation":
                subject = all_ner_labels[i]
                object = all_ner_labels[j]
                print(f"{subject} entity: {object} entity: {relation}")

    return all_ner_labels, all_re_labels

input_text = "Over-expression of HO-1 on mesenchymal stem cells promotes angiogenesis and improves myocardial function in infarcted myocardium Heme oxygenase-1 (HO-1) is a stress-inducible enzyme with diverse cytoprotective effects, and reported to have an important role in angiogenesis recently. Here we investigated whether HO-1 transduced by mesenchymal stem cells (MSCs) can induce angiogenic effects in infarcted myocardium. HO-1 was transfected into cultured MSCs using an adenoviral vector. 1 x 106 Ad-HO-1-transfected MSCs (HO-1-MSCs) or Ad-Null-transfected MSCs (Null-MSCs) or PBS was respectively injected into rat hearts intramyocardially at 1 h post-myocardial infarction. The results showed that HO-1-MSCs were able to induce stable expression of HO-1 in vitro and in vivo. The capillary density and expression of angiogenic growth factors, VEGF and FGF2 were significantly enhanced in HO-1-MSCs-treated hearts compared with Null-MSCs-treated and PBS-treated hearts. However, the angiogenic effects of HO-1 were abolished by treating the animals with HO inhibitor, zinc protoporphyrin. The myocardial apoptosis was marked reduced with significantly reduced fibrotic area in HO-1-MSCs-treated hearts; Furthermore, the cardiac function and remodeling were also significantly improved in HO-1-MSCs-treated hearts. Our current findings support the premise that HO-1 transduced by MSCs can induce angiogenic effects and improve heart function after acute myocardial infarction.  Introduction Recent pre-clinical and clinical studies have demonstrated that mesenchymal stem cells (MSCs) transplantation can attenuate ventricular remodeling and augment cardiac function when implanted into the infarcted myocardium. With an emerging interest to combine cell transplantation with gene therapy, MSCs are being assessed for their potential as carriers of exogenous therapeutic genes. Several studies have showed that genetic modification of donor cells prior to transplantation may result in their enhanced survival, better engraftment and improved restoration in infarcted hearts. Genetic modification MSCs with antiapoptotic Bcl-2 gene enhanced the survival of engrafted MSCs in the heart after acute myocardial infarction, ameliorated LV remodeling and improved LV function. Recent study shows that transplantation of MSCs transduced with Connexin43 gene into a rat MI model enhances MSCs survival, reduces infarct size, and improves contractile performance. MSCs over-expressing Akt limit infarct size and improve ventricular function, and the functional improvement occurs in < 72 h. However, improved survival of the cell graft may be less meaning if regional blood flow in the ischemic myocardium is not restored, especially expecting for long-term therapeutic effects. HO-1 is a stress-inducible rate-limiting enzyme that catalyzes the breakdown of pro-oxidant heme into biliverdin, carbon monoxide (CO) and free iron. Biliverdin can be reduced to bilirubin by biliverdin reductase. Several studies have shown that HO-1 is an anti-apoptotic and anti-oxidant enzyme, possessing cytoprotective activity under ischemic environment and increasing cell survival. Recently, studies have implicated a role for HO-1 in angiogenesis. Increasing expression of HO-1 can enhance proliferation and tube formation in human microvascular endothelial cells, and stromal cell-derived factor 1 promotes angiogenesis via a HO-1 dependent mechanism. Furthermore, local HO-1 inhibition blocks angiogenesis. Nevertheless, whether HO-1 transduced by MSCs has an effect on angiogenesis remains unclear. To test the hypothesis, we infected MSCs with recombinant adenovirus bearing human HO-1 (Adv-hHO-1) according to our previous protocols, and transplanted MSCs over-expressing HO-1 into acute myocardial infarction hearts. Our data indicate that over-expression of HO-1 in MSCs enhance angiogenesis and improves heart function in ischemic myocardium. Materials and methods Approval of animal experiments The animal experiments were conformed to the Guide for the Care and Use of Laboratory Animals published by the US National Institute of Health (NIH published No.85-23, revised 1996). Preparation of recombinant adenovirus A recombinant adenovirus containing human HO-1 (Adv-HO-1) was constructed as previously described. Briefly, a full-length human HO-1 gene cDNA was cloned into the adenovirus shuttle plasmid vector pAd-CMV, which contains a cytomegalovirus promoter and a polyadenylation signal of bovine growth hormone. For construction of adenovirus containing green fluorescent protein (GFP), a shuttle vector containing human phosphoglycerate kinase gene promoter was used. The control virus lacking the hHO-1 gene (Adv-null) was separately prepared. Recombinant adenovirus was generated by homologous recombination and propagated in 293 cells. At stipulated time, the supernatant from 293 cells was collected and purified on cesium chloride (CsCl) gradient centrifugation and stored in 10 mmol/L Tris-HCl (pH 7.4), 1 mmol/L MgCl2, and 10% (vol/vol) glycerol at -70 C until used for experiments. Virus titers were determined by a plaque assay on 293 cell monolayers.  Preparation of MSCs MSCs were isolated from bone marrow of adult Sprague-Dawley male rats and expanded according to reported protocols. Whole marrow cells were cultured at a density of 1 x 106 cells/cm2 in alpha-minimum essential medium (alpha-MEM, Gibco, USA) with 10% fetal bovine serum (FBS, Invitrogen, USA) and 100 mug/ml penicillin-streptomycin (Sigma, USA). The nonadherent cells were removed by a medium change at 72 h and every four days thereafter. After two passages, homogeneous MSCs that devoid of hematopoietic cells were used. A total of 1 x 106 cells/ml MSCs were plated in plates for 24 h. The medium was then replaced with serum free alpha-MEM containing indicated multiplicities of infection (MOI) of Adv-HO-1 or Adv-null. After incubation for 2 h, an equal volume of alpha-MEM containing 20% FBS was added to the medium and cell culture was continued for another 48 hours. To observe the nuclei of MSCs in vitro, sterile 4',6'-diamidino-2' phenylindole (DAPI) (Sigma, USA) stock solution was added to culture medium at a final concentration of 50 mug/ml for 30 min. After labeling, cells were washed six times in D-Hanks solution to remove unbound DAPI and then the cells were observed using fluorescent microscopy. Cell implantation and trafficking of the MSCs in vivo The male rats were anesthetized with sodium pentobarbital (40 mg/kg.i.p.), and mechanically ventilated. After the heart was exposed through a lateral thoracotomy, an 6-0 polypropylene thread was passed around the left coronary artery and the artery was occluded. Cyanosis and akinesia of the affected left ventricle were observed. The ECG was recorded to confirm the presence of infarction. One hour after myocardial infarction (MI), rats were randomly selected and approximately 1 x 106 HO-1MSCs or Null-MSCs in 0.1 ml of medium or equivalent volume of PBS alone was injected at four sites into the infarcted border zone using a 30-gauge needle (n = 12, each group). Some rats were given a daily intraperitoneal injection of the HO-1 inhibitor zinc-protoporphyrin (ZnPP, Porphyrin Products, Logan, UT, USA) at a concentration of 50 mumol/kg/day, starting two days before and continuing until 7 days after the HO-1-MSCs transplantation. Some rats were killed at 7 days after transplantation, and the treated hearts were harvested and cryopreserved in OCT media. Frozen tissue sections were used for histological examination of cell distribution.  Western blot MSCs were lysed in electrophoresis buffer (125 mmol/L Tris-HCl, pH 6.8, 12% glycerol, and 2% SDS), sonicated and boiled. Proteins (50 mug) were separated by sodium dodecyl sulfate polyacrylamide gel electrophoresis (SDS-PAGE), electrophoretically transferred to nitrocellulose membranes, and blocked with 1 x PBS containing Tween 20 (0.1%) and nonfat milk (5%) for 1 h. Then, the membranes were incubated with anti-HO-1 antibody (Santa Cruz, USA). Three weeks after transplantation, border regions of infarcted hearts from different groups were excised. Immunoblotting was performed using antibodies against VEGF or FGF2 (Santa Cruz, USA). Blots were developed by the ECL method (Pierce, USA), and relative protein levels were quantified by scanning densitometry and the relative gray value of protein = protein of interest/internal reference.  RT-PCR After 1 week of transplantation, the hearts was excised, and total RNA was extracted from the infarcted border zone using TRIzol reagent (Invitrogen, USA). The RT-PCR was performed as previously described. Immunohistochemistry Three weeks after transplantation, myocardial specimens were embedded in OCT compound (Sigma), then quickly frozen in liquid nitrogen and stored at -80 C. Cryostat sections were cut into 5-mum. For immunostaining, sections were incubated with anti alpha-smooth muscle actin (abCAM, USA). The sections were then incubated with appropriate secondary antibody. Five fields per section were randomly selected and analyzed at a magnification of 200. The number of capillaries was assessed from photomicrographs by computerized image analysis. TUNEL Staining To study the degree of cell apoptosis, TUNEL staining was performed using the In Situ Cell Death Detection Kit, POD (Roche, Germany) according to the manufacturer's instructions. For each heart, the total number of TUNEL-positive myocyte nuclei in the infarcted zone was counted in ten sections. Individual nuclei were visualized at a magnification of 200, and the percentage of apoptotic nuclei (apoptotic nuclei/total nuclei) was calculated in 6 randomly chosen fields per slide and averaged for statistical analysis. Measurement of hemodynamics 4 weeks after injection, hemodynamic measurements were made. In brief, rats were anesthetized with pentobarbital sodium (60 mg/kg, i.p.). Catheter (model SPR-320, Millar, Inc.) filled with heparinized (10 U/ml) saline solution was placed in the right carotid artery and then advanced retrogradely into the LV. Hemodynamic parameters were recorded by a phyisiogical recorder (RJG-4122, Nihon Kohden, Japan). Assessment of Fibrosis After 4 weeks of injection, the hearts were harvested, washed in PBS, and fixed in 10% formalin overnight at 4 C. Paraffin embedded tissues were cut into 5-mum sections and stained by Masson's Trichrome staining (Sigma) for collagen determination. Five fields per section were calculated and the collagen-delegated infarction percentage was analyzed by a blinded investigator. The calculation formula used for the infracted size is: % infarct size = infarct areas/total left ventricle area x 100%.   Statistics At least three independent experiments were carried out. Each data point was presented as mean +- SD. Statistical significance was evaluated using one-way ANOVA. A value of P < 0.05 was considered statistically significant. Results MSCs mediated HO-1 over-expression in vitro and in vivo MSCs isolated from rat bone marrow were infected with Adv-HO-1, and strong expression of GFP was observed by fluorescence analysis (Fig. 1A). The over-expression of HO-1 was confirmed by Western blotting (Fig. 1B). Levels of HO-1 in HO-1-MSCs were significantly higher than that in MSCs and Null-MSCs. At 7 days post-transplantation, the HO-1-MSCs were embedded into the host myocardium (Fig. 1C). The expression of HO-1 in hearts was confirmed by relative quantification of hHO-1 mRNA (Fig. 1D). The hHO-1 mRNA was detected in the cardiac sample extracted from cardiac tissue of HO-1-MSCs group rather than in the Null-MSCs and PBS group. HO-1 expression mediated by MSCs in Vitro and Vivo. (A) HO-1 expression mediated by MSCs with GFP in Vitro (200x). (B) Western blot analysis of HO-1 protein in MSCs with actin used as an internal control. Lane a, MSCs control (untransfected); lane b, Null-MSCs; lane c, Adv-HO-1-MSCs. (C) Graph showing the relative fold induction of HO-1 protein levels in MSCs, n = 6. * P < 0.05 compared with MSCs control (untransfected); &P > 0.05 compared with MSCs control (untransfected); # P < 0.05 compared with Null-MSCs. (D) Image from grafted HO-1-MSCs in the infarcted myocardium (200x). (E) RT-PCR detection mRNA in cardiac tissue. Lane a, MSCs control (untransfected); lane b, Null-MSCs; lane c, Adv-HO-1-MSCs.   Effects of HO-1-MSCs transplantation on angiogenesis Immunofluorescent staining for alpha-smooth muscle actin and quantification of capillary density revealed that the capillary density was significantly enhanced by HO-1-MSCs transplantation compared with Null-MSCs and PBS transplantation; and the capillary density was also significantly enhanced by Null-MSCs transplantation compared with by PBS transplantation (Fig 2A, B). To determine whether expression of HO-1 mediated by MSCs results in angiogenesis and to minimize the impacts on angiogenesis induced by MSCs in this study, we investigated the effect of an HO inhibitor, ZnPP, on the HO-1-MSCs group. ZnPP treatment abolished the increase in capillary density. There was not significant difference between Null-MSCs group and ZnPP treated HO-1-MSCs group (Fig 2A, B). Similarly, the expressions of angiogenic factors VEGF and FGF2 were significantly higher in HO-1-MSCs group compared with Null-MSCs group and ZnPP treated HO-1-MSCs group; The expression of VEGF and FGF2 did not differ between Null-MSCs group and ZnPP treated HO-1-MSCs group (Fig. 2C). Effects of HO-1-MSCs transplantation on neovascularization and angiogenic growth factors. (A) Representative microvessel in the border of infarcted myocardium 3 weeks after transplantation (200x). (B) Values are means +- SD of data from 6 separate experiments, * P < 0.05 compared with the hearts treated with PBS. # P < 0.05 compared with the hearts treated with Null-MSCs. &P > 0.05 compared with the hearts treated with Null-MSCs. $ P < 0.05 compared with the hearts treated with HO-1-MSCs and HO inhibitor. Lane a, hearts treated with PBS; Lane b, hearts treated with Null-MSCs; Lane c, hearts treated with HO-1-MSCs and HO inhibitor; Lane d, hearts treated with HO-1-MSCs. (C) Blots regarding the expression of FGF2, VEGF and actin were developed by the ECL method and relative protein levels were quantified by scanning densitometry and the relative gray value of protein = protein of interest/internal reference. Values are means +- SD of data from 6 separate experiments, * P < 0.05 compared with the hearts treated with PBS. # P < 0.05 compared with the hearts treated with Null-MSCs. &P > 0.05 compared with the hearts treated with Null-MSCs. $ P < 0.05 compared with the hearts treated with HO-1-MSCs and HO inhibitor. Lane a, hearts treated with PBS; Lane b, hearts treated with Null-MSCs; Lane c, hearts treated with HO-1-MSCs and HO inhibitor; Lane d, hearts treated with HO-1-MSCs.  Effects of HO-1-MSCs transplantation on myocyte apoptosis The degree of myocyte apoptosis as assessed by TNUEL was significantly less in the HO-1-MSCs group than other groups, and there was no significant difference between Null-MSCs group and ZnPP treated HO-1-MSCs group. TUNEL positive nuclei were also less in Null-MSCs group and ZnPP treated HO-1-MSCs group than that in PBS group (Fig. 3A, B). Effects of HO-1-MSCs transplantation on apoptosis. (A) TUNEL-positive cells in the border zone of infracted myocardium 3 weeks after transplantation (100x). (B) Values are means +- SD of data from 6 separate experiments, * P < 0.05 compared with the hearts treated with PBS. # P < 0.05 compared with the hearts treated with Null-MSCs. &P > 0.05 compared with the hearts treated with Null-MSCs. $ P < 0.05 compared with the hearts treated with HO-1-MSCs and HO inhibitor. Lane a, normal control; Lane b, hearts treated with PBS; Lane c, hearts treated with Null-MSCs; Lane d, hearts treated with HO-1-MSCs and HO inhibitor; Lane e, hearts treated with HO-1-MSCs.  Effects of HO-1-MSCs transplantation on ventricular function and fibrosis Hemodynamic parameters were measured 4 weeks after transplantation. LV function in HO-1-MSCs and Null-MSCs group was improved significantly compared with that in PBS group, and there was significant difference between HO-1-MSCs and Null-MSCs group (Fig. 4). The typical left ventricle wall sections after Masson-Trichome staining were shown on Fig. 5A, C. The percentage of fibrosis in the HO-1-MSCs and Null-MSCs group was significantly reduced compared with PBS group, which was the lowest in HO-1-MSCs group (Fig. 5B). Effects of HO-1-MSCs transplantation on ventricular function. (A) Hemodynamic assessment of cardiac function at 4 weeks after transplantation. LVSP: left ventricle systolic pressure; LVEDP: left ventricle end-diastolic pressure; + dP/dtmax and -dP/dtmax: rate of rise and fall of ventricular pressure, respectively. means +- SD of data from 6 separate experiments, *P < 0.05 compared with the hearts treated with PBS, #P < 0.05 compared with the hearts treated with Null-MSCs. Lane a, hearts treated with PBS; Lane b, hearts treated with Null-MSCs; Lane c, hearts treated with HO-1-MSCs. Effects of HO-1-MSCs transplantation on ventricular remodeling. (A) The transmural slices of the left ventricle were stained with Masson trichrome (1.25x). (B) % fibrotic area in heart with infarction was measured. Values are means +- SD of data from 6 separate experiments, *P < 0.05 compared with the hearts treated with PBS, #P < 0.05 compared with the hearts treated with Null-MSCs. (C) The border zone of the infarct area (100x).   Discussion Under most circumstance, the treatment of MI by using MSCs showed poor survival of transplanted cells. In addition to the quick loss of cells within 24 h of transplantation caused by cell leakage into the extra myocardial space, or being flushed out in the coronary vein, the molecular mechanism for cell death in ischemic myocardium may include ischemia, ischemic/reperfusion, and more importantly the host inflammatory response mediators and proapoptotic factors in the ischemic myocardium. It has been showed that inflammatory process after MI peaks at 1 week, and apoptosis is a major factor causing donor cell death. Many studies point to the anti-apoptotic and anti-inflammatory effects. It is clear that angiogenesis cannot only improve the survival of transplanted cells, but also reduce myocardial apoptosis and restores the heart function. MSCs were reported to have the potential to release several kinds of cytokines, which induce angiogenesis. However, the number of cells at 3 weeks after transplantation decreased significantly, and almost all transplantation cells seemed to be lost at 6 weeks. Limited MSCs cannot achieve maximum functional benefits of angiogenesis. HO-1 has been recognized to be involved in diverse cytoprotective effects, due to its multiple catalytic byproducts. HO-1 was administered to improve the survival environment of MSCs and to achieve maximum functional benefits of MSCs. Recent studies showed that over-expression of the HO-1 gene in endothelial cell caused a significant increase in angiogenesis. Adenovirus-mediated HO-1 gene transfer into the ischemic hindlimb facilitated a significant recovery of blood flow in the hindlimb, and this effect was, at least in part, due to an increase in the capillary density, thus, to angiogenic effects of HO-1. In our study, capillary density and the expression of angiogenic growth factors, including vascular endothelial growth factor (VEGF) and fibroblast growth factor 2 (FGF2), in the border area of the infarct in HO-1-MSCs group was significantly higher than that in Null-MSCs group and ZnPP treated HO-1-MSCs group. However, capillary density and the expression of VEGF and FGF2 did not show significant difference between Null-MSCs and ZnPP treated HO-1-MSCs group, indicating the role of HO-1 in the induction of angiogenesis. We confirmed that HO-1 transduced by MSCs also have positive effects on angiogenesis. It has been reported that nitric oxide (NO) may modulate angiogenesis by upregulating VEGF in vascular cells, and NO inhibitors can reduce the angiogenic potential of endothelial cells. CO may also be involved in the expression of VEGF. Another contributor to enhance angiogenesis may be the increasing expression of angiogenic growth factors in the ischemic myocardium. VEGF is a strong therapeutic reagent by inducing angiogenesis in ischemic myocardium, and VEGF can mediate the ischemia-induced mobilization of bone marrow stem cells. In addition, FGF2 also have the potential to promote angiogenesis, and regulate proliferation, migration, differentiation of vascular cells. Lin'study showed that HO-1 gene transfer post MI provides protection at least in part by promoting angiogenesis through inducing angiogenic growth factors. Angiogenesis contributes to the regional blood flow in the ischemic myocardium. Cardiomyocytes death plays an important role in the development of remodeling; ventricular remodeling with chamber dilatation and wall thinning are important features of post-infarction cardiac function. Studies have shown that late reperfusion after infarction results in enhanced cardiac function and remodeling. The improved blood supply may result in salvaging of cardiomyocytes that would otherwise be lost or no-functional due to ischemia. In addition, VEGF, may provide myocardial protection, blocking the programmed cell death response that is know to contribute significantly to the development of ischemic heart failure. In the current study, significant decrease of apoptotic cells in HO-1-MSCs group was observed as compared with that of control groups, and the enlargement of LV dilatation and fibrosis were significantly decreased in HO-1-MSCs group with smaller chambers and thicker LV anterior walls. Echocardiographic results further confirmed our hypothesis that HO-1 modified MSCs significantly improve LV function. In conclusion, HO-1 transduced by MSCs can induce angiogenic effects and improve heart function after acute myocardial infarction Competing interests The authors declare that they have no competing interests. Authors' contributions BZ designed, carried out the main experiment and drafted the manuscript. GS-L helped to design the experiment and drafted the manuscript. XF-R helped to finish the statistical analysis and improve the manuscript. YZ participated in RT-PCR and Western blot analysis. HL-Ch helped to finish histological experiments. All authors read and approved the final manuscript. Acknowledgements We thank Dr. Lee-young Chau for generously providing the Adv-hHO-1 and kind experimental helps. This work was supported by the Chinese National Nature Science Foundation (30900609) Extracardiac approaches to protecting the heart Bcl-2 engineered MSCs inhibited apoptosis and improved heart function Connexin43 promotes survival of mesenchymal stem cells in ischaemic heart Evidence supporting paracrine hypothesis for Akt-modified mesenchymal stem cell-mediated cardiac protection and functional improvement The enzymatic conversion of heme to bilirubin by microsomal heme oxygenase Effect of heme and heme oxygenase-1 on vascular endothelial growth factor synthesis and angiogenic potency of human keratinocytes Stromal cell-derived factor 1 promotes angiogenesis via a heme oxygenase 1-dependent mechanism Significance of heme oxygenase in prolactin-mediated cell proliferation and angiogenesis in human endothelial cells Paracrine action of HO-1-modified mesenchymal stem cells mediates cardiac protection and functional improvement Adenovirus-mediated heme oxygenase-1 gene transfer inhibits the development of atherosclerosis in apolipoprotein E-deficient mice Apoptosis in experimental myocardial infarction in situ and in the perfused heart in vitro Experimental myocardial infarction in the rat: qualitative and quantitative changes during pathologic evolution Role of MAP kinases in nitric oxide induced muscle-derived adult stem cell apoptosis Effective engraftment but poor mid-term persistence of mononuclear and mesenchymal bone marrow cells in acute and chronic rat myocardial infarction Improved graft mesenchymal stem cell survival in ischemic heart with a hypoxia-regulated heme oxygenase-1 vector Effect of heme and heme oxygenase-1 on vascular endothelial growth factor synthesis and angiogenic potency of human keratinocytes Facilitated angiogenesis induced by heme oxygenase-1 gene transfer in a rat model of hindlimb ischemia Nitric oxide induces the synthesis of vascular endothelial growth factor by rat vascular smooth muscle cells Heme oxygenase and angiogenic activity of endothelial cells: stimulation by carbon monoxide and inhibition by tin protoporphyrin-IX Simultaneous surgical revascularization and angiogenic gene therapy in diffuse coronary artery disease Additive effect of endothelial progenitor cell mobilization and bone marrow mononuclear cell transplantation on angiogenesis in mouse ischemic limbs Fibroblast growth factors: at the heart of angiogenesis Effect of FGF-1 and FGF-2 on VEGF binding to human umbilical vein endothelial cells Heme oxygenase-1 promotes neovascularization in ischemic heart by coinduction of VEGF and SDF-1 A pressure overload model to track the molecular biology of heart failure Role of calcineurin in Porphyromonas gingivalis-induced myocardial cell hypertrophy and apoptosis Lipopolysaccharide preconditioning enhances the efficacy of mesenchymal stem cells transplantation in a rat model of acute myocardial infarction Transmyocardial laser revascularization combined with vascular endothelial growth factor 121 (VEGF121) gene therapy for chronic myocardial ischemia--do the effects really add up?"
ner_labels, re_labels = extract_relationships_large_text(input_text, model, tokenizer, label_to_id, relation_to_id)


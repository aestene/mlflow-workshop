import pandas as pd
import numpy as np
import string
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.preprocessing import StandardScaler, MinMaxScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, classification_report, f1_score, auc as sk_auc, roc_curve, precision_score, recall_score, accuracy_score
from sklearn.pipeline import make_pipeline
from sklearn.decomposition import TruncatedSVD
from sklearn.model_selection import train_test_split, StratifiedKFold
from sklearn.naive_bayes import MultinomialNB
import pickle
import random
import re
import os
import argparse
import mlflow
from mlflow import log_metric, log_param, log_artifact
import mlflow.sklearn
import mlflow.pyfunc
import matplotlib.pyplot as plt 
import seaborn as sns 

class TextClassifier(mlflow.pyfunc.PythonModel):

    def __init__(self, vectorizer, clf):
        self.vectorizer = vectorizer
        self.clf = clf
        
    def predict(self, context, model_input):
        print(model_input)
        print(type(model_input))
        prepped_input = self.vectorizer.transform(model_input["description"])
        print(prepped_input.shape)
        preds = self.clf.predict_proba(prepped_input)
        resp = []
        for pred in preds:
            pred_resp = {c:p for c, p in zip(clf.classes_, pred)}
            resp.append(pred_resp)
        return resp

parser = argparse.ArgumentParser()
parser.add_argument("-s", "--seed", help="Specify seed for reproducibility", type=int, default=42)
parser.add_argument("-i", "--idf", help="Whether to use inverse document frequency", type=bool, default=True)
parser.add_argument("-n", "--ngrams", help="Number of character sequences to use as features", type=int,
                    choices=[1,2,3], default=1)
parser.add_argument("-u", "--use_stoplist", help="Whether to remove most common words", type=bool, default=True)
parser.add_argument("-d", "--datafile", help="Path to datafile", type=str, default="mlflow/data/course_descriptions.csv")
parser.add_argument("-m", "--model", help="Model type", type=str, choices=["nb", "log_reg"], default="nb")
# Naive Bayes args
parser.add_argument("-a", "--alpha", help="Naive bayes smoothing parameter (0=no smoothing)", type=float, default=1.)
parser.add_argument("-f", "--fit_prior", help="Whether to fit priors", type=bool, default=True)
# Logreg args 
parser.add_argument("-C", "--C", help="Logistic regression regularization parameter", type=float, default=1.)
parser.add_argument("-b", "--backend", help="Whether training is running local or on databricks", type=str, choices=["databricks", "local"], default="databricks")


if __name__ == "__main__":

    args = parser.parse_args()
    print("Using arguments:")
    print(args)
    seed = args.seed
    use_idf = args.idf
    ngram_range = (1,args.ngrams)
    alpha = args.alpha
    fit_prior = args.fit_prior
    use_stoplist = args.use_stoplist
    use_svd = False
    C = args.C
    model_dict = {"nb":MultinomialNB(alpha=alpha, fit_prior=fit_prior),
                  "log_reg": LogisticRegression(C=C, multi_class="ovr", solver="saga", max_iter=1000)}
    clf = model_dict[args.model]
    backend = args.backend

    conda_env_path = "condaenv.yml"
    model_prefix = "/models"
    data_file = args.datafile
    if backend=="databricks":
        exp_name = "/Users/arnts@equinor.com/MLflow_workshop"
    elif backend=="local":
        exp_name = "ntnu_course_classifier"

    mlflow.set_experiment(exp_name)

    with mlflow.start_run() as run:
        # Get path to save model
        tracking_uri = mlflow.tracking.get_tracking_uri() 
        print("Logging to "+tracking_uri)
        artifact_uri = mlflow.get_artifact_uri()
        if artifact_uri.startswith("file:///"):
            artifact_uri = artifact_uri.split("file:///")[1]
        print("Saving artifacts to "+artifact_uri)
        model_path = artifact_uri+model_prefix

        # Log params
        mlflow.log_param("seed", seed)
        mlflow.log_param("use_idf", use_idf)
        mlflow.log_param("use_stoplist", use_stoplist)
        mlflow.log_param("ngrams", args.ngrams)
        mlflow.log_param("data_file", data_file)
        mlflow.log_param("model_type", args.model)
        #if args.model == "nb":
        mlflow.log_param("alpha", alpha)
        mlflow.log_param("fit_prior", fit_prior)
        #if args.model == "log_reg":
        mlflow.log_param("C", C)

        ## Data import and cleaning
        df = pd.read_csv(data_file, usecols=[1,2,3,4,5,6])
        df = df.dropna()

        def remove_punctuation(document):
            # Remove weird characters
            return "".join([ (c if c not in string.punctuation+"\n\r\t" else " ") for c in document])

        def tokenize(document):
            # From document to list of lower case tokens
            return [w.lower() for w in remove_punctuation(document).split(" ") if len(w)>0]

        if use_stoplist:
            stoplist = [l.strip() for l in open("stopwords.txt", "r").readlines()]
        else:
            stoplist = None

        # Create pandas Series with target variable 
        y = df["fac"].astype(str)

        # Split into training and test set
        X_train, X_test, y_train, y_test = train_test_split(df["description"], y, stratify=y, random_state=seed)

        # Initialize the TfidfVectorizer
        vec = TfidfVectorizer(tokenizer=tokenize, stop_words=stoplist, use_idf=use_idf, ngram_range=ngram_range)
        
        if use_svd:
            svd = TruncatedSVD(n_components=100, n_iter=50)
            scaler = MinMaxScaler()
            vec = make_pipeline(vec, svd, scaler)

        # Fit to training set and transform test set
        trn_vec= vec.fit_transform(X_train.values)
        test_vec = vec.transform(X_test.values)

        # Create a list of the unique target values
        label_cols = df["fac"].astype(str).unique().tolist()

        # Fit the model
        clf.fit(trn_vec, y_train)

        # Make predictions with test set
        preds = clf.predict(test_vec)

        textclf = TextClassifier(vec, clf)
        print("Saving model to "+model_path)
        mlflow.pyfunc.save_model(model_path, conda_env=conda_env_path, python_model=textclf)
        
        # Calculate metrics from predictions and y_test
        acc = accuracy_score(y_test, preds)
        f1_macro = precision_score(y_test, preds, average="macro")
        f1_micro = precision_score(y_test, preds, average="micro")
        f1_per_class = precision_score(y_test, preds, labels=label_cols, average=None)
        cm = confusion_matrix(y_test, preds, labels=label_cols)
        df_cm = pd.DataFrame(cm, index=label_cols, columns=label_cols)
        cm_plot = sns.heatmap(df_cm, annot=True, fmt="d")
        heatmap_path = artifact_uri+"/cm_heatmap.png"
        fig = cm_plot.get_figure()
        fig.savefig(heatmap_path)

        mlflow.log_metric("accuracy", acc)
        mlflow.log_metric("f1_macro", f1_macro)
        mlflow.log_metric("f1_micro", f1_micro)
        for c, f1 in zip(label_cols, f1_per_class):
            mlflow.log_metric("f1_"+c, f1)        
        #mlflow.sklearn.save_model(clf, model_path, conda_env=conda_env_path, serialization_format=mlflow.sklearn.SERIALIZATION_FORMAT_CLOUDPICKLE)

        #%% [markdown]
        # ## Next steps
        # - Define metrics
        # - Plot
        # - Script evolution
        # - Think about steps





"""
Helper script called by deploy_setup.ps1 to perform MLflow operations.

Usage:
    python deploy_mlflow_helper.py log   --host HOST --token TOKEN --model MODEL --experiment PATH --code PATH
    python deploy_mlflow_helper.py register --host HOST --token TOKEN --model-uri URI --model-name NAME
"""

import argparse
import os
import sys


def cmd_log(args):
    """Log the agent model to MLflow and print run_id + model_uri."""
    import mlflow

    os.environ["DATABRICKS_HOST"] = args.host
    os.environ["DATABRICKS_TOKEN"] = args.token
    os.environ["DATABRICKS_MODEL"] = args.model

    mlflow.set_tracking_uri("databricks")
    mlflow.set_experiment(args.experiment)

    with mlflow.start_run(run_name="langgraph-agent-cli") as run:
        model_info = mlflow.langchain.log_model(
            lc_model=args.code,
            name="langgraph_agent",
            input_example={"messages": [{"role": "user", "content": "What is RAG?"}]},
        )
        print(f"__RUN_ID__={run.info.run_id}")
        print(f"__MODEL_URI__={model_info.model_uri}")


def cmd_register(args):
    """Register the logged model in Unity Catalog and print the version."""
    import mlflow

    os.environ["DATABRICKS_HOST"] = args.host
    os.environ["DATABRICKS_TOKEN"] = args.token

    mlflow.set_tracking_uri("databricks")
    mlflow.set_registry_uri("databricks-uc")

    registered = mlflow.register_model(
        model_uri=args.model_uri,
        name=args.model_name,
    )
    print(f"__MODEL_VERSION__={registered.version}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MLflow deployment helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # log subcommand
    log_parser = subparsers.add_parser("log", help="Log agent model to MLflow")
    log_parser.add_argument("--host", required=True)
    log_parser.add_argument("--token", required=True)
    log_parser.add_argument("--model", required=True)
    log_parser.add_argument("--experiment", required=True)
    log_parser.add_argument("--code", required=True)

    # register subcommand
    reg_parser = subparsers.add_parser("register", help="Register model in Unity Catalog")
    reg_parser.add_argument("--host", required=True)
    reg_parser.add_argument("--token", required=True)
    reg_parser.add_argument("--model-uri", required=True)
    reg_parser.add_argument("--model-name", required=True)

    args = parser.parse_args()

    if args.command == "log":
        cmd_log(args)
    elif args.command == "register":
        cmd_register(args)

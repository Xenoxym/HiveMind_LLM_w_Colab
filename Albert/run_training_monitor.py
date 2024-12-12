#!/usr/bin/env python3

import time
from dataclasses import asdict, dataclass, field
from ipaddress import ip_address
from typing import Optional

import requests
import torch
import wandb
from torch_optimizer import Lamb
from transformers import AlbertConfig, AlbertForPreTraining, HfArgumentParser, get_linear_schedule_with_warmup

import hivemind
from hivemind.optim.state_averager import TrainingStateAverager
from hivemind.utils.logging import get_logger, use_hivemind_log_handler
from hivemind.utils.networking import log_visible_maddrs

import utils
from arguments import AveragerArguments, BaseTrainingArguments, OptimizerArguments

use_hivemind_log_handler("in_root_logger")
logger = get_logger(__name__)


@dataclass
class TrainingMonitorArguments(BaseTrainingArguments):
    """
    Note: You might want to have several initial peers so that if one dies,
    new workers still can join the collaboration via alive initial peers' addresses.
    Specify initial_peers argument for that purpose
    """

    use_google_dns: bool = field(
        default=False,
        metadata={
            "help": "Use Google DNS to determine the public IP address of this machine (and add it to --announce_maddrs)"
        },
    )
    refresh_period: float = field(default=30, metadata={"help": "Period (in seconds) for fetching the keys from DHT"})
    wandb_project: Optional[str] = field(
        default=None, metadata={"help": "Name of Weights & Biases project to report the training progress to"}
    )
    store_checkpoints: bool = field(default=True, metadata={"help": "If False, disables periodic checkpoint saving"})
    save_checkpoint_step_interval: int = field(
        default=5, metadata={"help": "Frequency (in steps) of fetching and saving state from peers"}
    )
    model_config_path: str = field(
        default="albert-large-v2",
        metadata={"help": "Path to the model config"},
    )
    repo_path: Optional[str] = field(
        default=None, metadata={"help": "Path to local repository to store the model and optimizer states"}
    )
    repo_url: Optional[str] = field(
        default=None, metadata={"help": "URL of Hugging Face Hub repository to upload the model and optimizer states"}
    )
    upload_interval: Optional[float] = field(
        default=None, metadata={"help": "Frequency (in seconds) of uploading the model to Hub"}
    )


class CheckpointHandler:
    def __init__(
        self,
        monitor_args: TrainingMonitorArguments,
        optimizer_args: OptimizerArguments,
        averager_args: AveragerArguments,
        dht: hivemind.DHT,
    ):
        self.save_checkpoint_step_interval = monitor_args.save_checkpoint_step_interval
        self.repo_path = monitor_args.repo_path
        self.repo_url = monitor_args.repo_url
        self.upload_interval = monitor_args.upload_interval
        self.previous_step = -1

        config = AlbertConfig.from_pretrained(monitor_args.model_config_path)
        self.model = AlbertForPreTraining(config)

        no_decay = ["bias", "LayerNorm.weight"]
        optimizer_grouped_parameters = [
            {
                "params": [p for n, p in self.model.named_parameters() if not any(nd in n for nd in no_decay)],
                "weight_decay": 0.01,
            },
            {
                "params": [p for n, p in self.model.named_parameters() if any(nd in n for nd in no_decay)],
                "weight_decay": 0.0,
            },
        ]

        opt = Lamb(
            optimizer_grouped_parameters,
            lr=0.00176,
            weight_decay=0.01,
            clamp_value=10000.0,
            debias=True,
        )

        self.state_averager = TrainingStateAverager(
            dht=dht,
            optimizer=opt,
            scheduler=get_linear_schedule_with_warmup(opt, num_warmup_steps=5000, num_training_steps=125_000),
            prefix=f"{run_id}_state_averager",
            state_compression=hivemind.Float16Compression(),
            bandwidth=optimizer_args.bandwidth,
            client_mode=optimizer_args.client_mode,
            start=True,
            **asdict(averager_args),
        )
        self.previous_timestamp = time.time()

    def is_time_to_save_state(self, cur_step):
        if self.save_checkpoint_step_interval is None:
            return False
        elif cur_step - self.previous_step >= self.save_checkpoint_step_interval:
            return True
        else:
            return False

    def save_state(self, cur_step):
        logger.info("Saving state from peers")
        self.state_averager.load_state_from_peers()
        self.previous_step = cur_step

    def is_time_to_upload(self):
        if self.repo_path is None:
            return False
        elif time.time() - self.previous_timestamp >= self.upload_interval:
            return True
        else:
            return False

    def upload_checkpoint(self, current_loss):
        logger.info("Saving optimizer")
        torch.save(self.state_averager.optimizer.state_dict(), f"{self.repo_path}/optimizer_state.pt")
        self.previous_timestamp = time.time()
        logger.info("Started uploading to Model Hub")
        self.model.push_to_hub(
            repo_name=self.repo_path,
            repo_url=self.repo_url,
            commit_message=f"Step #{current_step}, loss {current_loss:.3f}",
        )
        logger.info("Finished uploading to Model Hub")


if __name__ == "__main__":
    parser = HfArgumentParser((TrainingMonitorArguments, OptimizerArguments, AveragerArguments))
    monitor_args, optimizer_args, averager_args = parser.parse_args_into_dataclasses()

    print(f"monitor_args={monitor_args}")
    

    if monitor_args.use_google_dns:
        request = requests.get("https://api.ipify.org")
        request.raise_for_status()

        address = request.text
        logger.info(f"Received public IP address of this machine: {address}")

        print(f"Received public IP address of this machine: {address}")

        version = ip_address(address).version
        monitor_args.announce_maddrs += [f"/ip{version}/{address}/tcp/0"]

        print(f"version = ip_address(address).version = /ip{version}/{address}/tcp/0")

    print(f"monitor_args.run_id={monitor_args.run_id}")
    run_id = monitor_args.run_id
    validators, local_public_key = utils.make_validators(run_id)

    print(f"Before: {monitor_args.announce_maddrs}")


    from pyngrok import ngrok  # 安装 pyngrok: pip install pyngrok

    # # 在 DHT 初始化之前，创建 ngrok 隧道
    # dht_port = 43339  # 替换为你希望的 DHT 端口
    # ngrok_tunnel = ngrok.connect(dht_port, "tcp")
    # public_url = ngrok_tunnel.public_url
    # logger.info(f"Ngrok tunnel created: {public_url}")

    # # 修改 announce_maddrs 参数
    # monitor_args.announce_maddrs = [f"/ip4/0.0.0.0/tcp/{dht_port}"]

    # print(f"After: {monitor_args.announce_maddrs}")

    # 初始化 DHT
    dht = hivemind.DHT(
        start=True,
        initial_peers=monitor_args.initial_peers,
        record_validators=validators,
        use_ipfs=monitor_args.use_ipfs,
        host_maddrs=monitor_args.host_maddrs,
        announce_maddrs=monitor_args.announce_maddrs,
        identity_path=monitor_args.identity_path,
    )
    
    # dht = hivemind.DHT(
    #     start=True,
    #     listen_on=coordinator_args.dht_listen_on,
    #     endpoint=f"{coordinator_args.address}:*",
    #     initial_peers=coordinator_args.initial_peers,
    #     record_validators=validators,
    # )

    import re
    # Get the visible multiaddresses directly
    visible_maddrs = dht.get_visible_maddrs()
    print(visible_maddrs)

    # Regular expression to match port number in the Multiaddr format
    port_pattern = r"tcp/(\d+)"

    # Extract port numbers
    ports = []
    for addr in visible_maddrs:
        # Convert Multiaddr object to string and apply regex
        addr_str = str(addr)  # Convert Multiaddr object to a string
        match = re.search(port_pattern, addr_str)
        if match:
            ports.append(int(match.group(1)))

    # Print the extracted ports
    print(ports)


    # 获取 DHT 使用的端口号
    dht_port = ports[0]  # 假设 DHT 类有一个 `port` 属性，表示 DHT 服务的端口号

    # 启动 ngrok 隧道，将端口暴露到外部
    ngrok_tunnel = ngrok.connect(dht_port, "tcp")
    public_url = ngrok_tunnel.public_url

    # 打印 Ngrok 隧道的公共 URL
    logger.info(f"Ngrok tunnel created: {public_url}")

    # 修改 announce_maddrs 参数
    monitor_args.announce_maddrs = [f"/ip4/0.0.0.0/tcp/{dht_port}"]

    print(f"After: {monitor_args.announce_maddrs}")

    # 显示可见的多重地址
    log_visible_maddrs(dht.get_visible_maddrs(), only_p2p=monitor_args.use_ipfs)

    # 打印 Ngrok 公共地址
    logger.info(f"DHT is now accessible via: {public_url}")

    # 在后台启动 ngrok 进程并输出日志
    ngrok_command = f"nohup ./ngrok tcp {dht_port} > ngrok.log 2>&1 &"

    import subprocess
    subprocess.run(ngrok_command, shell=True)




    if monitor_args.wandb_project is not None:
        wandb.init(project=monitor_args.wandb_project)

    current_step = 0
    if monitor_args.store_checkpoints:
        checkpoint_handler = CheckpointHandler(monitor_args, optimizer_args, averager_args, dht)

    # checkpoint_handler = CheckpointHandler(monitor_args, optimizer_args, averager_args, dht)

    time_x = 0
    # while True:
    while time_x<3:
        print(f"While True:{time_x}")
        metrics_dict = dht.get(run_id + "_metrics", latest=True)
        print(f"metrics_dict = {metrics_dict}")
        if metrics_dict is not None:
            metrics_dict = metrics_dict.value
            metrics = [utils.LocalMetrics.parse_obj(metrics_dict[peer].value) for peer in metrics_dict]
            latest_step = max(item.step for item in metrics)

            if latest_step != current_step:
                logger.debug(f"Got metrics from {len(metrics)} peers")

                for i, metrics_for_peer in enumerate(metrics):
                    logger.debug(f"{i} peer {metrics_for_peer}")

                current_step = latest_step
                alive_peers = 0
                sum_loss = 0
                num_samples = 0
                sum_perf = 0
                sum_mini_steps = 0

                for item in metrics:
                    sum_loss += item.loss
                    alive_peers += 1
                    sum_perf += item.samples_per_second
                    num_samples += item.samples_accumulated
                    sum_mini_steps += item.mini_steps
                current_loss = sum_loss / sum_mini_steps
                logger.info(f"Step #{current_step}\tloss = {current_loss:.5f}")

                if monitor_args.wandb_project is not None:
                    wandb.log(
                        {
                            "loss": current_loss,
                            "alive peers": alive_peers,
                            "samples": num_samples,
                            "performance": sum_perf,
                            "step": latest_step,
                        }
                    )

                print(f"monitor_args.store_checkpoints = {monitor_args.store_checkpoints}")
     
                if monitor_args.store_checkpoints:

                    if checkpoint_handler.is_time_to_save_state(current_step):
                        checkpoint_handler.save_state(current_step)
                        if checkpoint_handler.is_time_to_upload():
                            checkpoint_handler.upload_checkpoint(current_loss)
        logger.debug("Peer is still alive...")
        time.sleep(monitor_args.refresh_period)
        time_x += 1

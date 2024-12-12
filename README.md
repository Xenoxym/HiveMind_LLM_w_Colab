# Training ALBERT with decentralized averaging

This tutorial will walk you through the steps to set up collaborative training with the ALBERT-large-v2 model and the
WikiText103 dataset. It uses Hugging Face [datasets](https://github.com/huggingface/datasets)
and [transformers](https://github.com/huggingface/transformers/) libraries to compute local updates,
using `hivemind.Optimizer` to exchange information between peers.

## Preparation

* Apply for ngrok. Since COLAB is a virtual environment, we cannot directly obtain a public network address. We need to prepare an ngrok account and obtain the ngrok authtoken. Run '!./ngrok authtoken YOUR_NGROK_AUTHTOKEN' 
  * Make sure to replace YOUR_NGROK_AUTHTOKEN with the actual authtoken from your ngrok account.   
* Install hivemind: `pip install git+https://github.com/learning-at-home/hivemind.git`
* Dependencies: `pip install -r requirements.txt`
* Preprocess data: `./tokenize_wikitext103.py`

## Running an experiment

### First peer

Run the first DHT peer to welcome trainers and record training statistics (e.g., loss and performance):

- In this example, we use [wandb.ai](https://wandb.ai/site) to plot training metrics. If you're unfamiliar with Weights
  & Biases, here's a [quickstart tutorial](https://docs.wandb.ai/quickstart).
- Run `./run_training_monitor.py --wandb_project YOUR_WANDB_PROJECT`

  - `YOUR_WANDB_PROJECT` is a name of wandb project used to track training metrics. Multiple experiments can have the
    same project name.

- Run the trainer:`./run_trainer.py`
  
- You will see output similar to:
```
--initial_peers /ip4/x.x.x.x/tcp/xxxxx/p2p/ABCDE
Ngrok tunnel created: tcp://x.tcp.ngrok.io:12345
```

Important: These details will be used by other GPUs to join the training process.

### New Trainers

To join the collaboration with a GPU trainer,

- Install the same dependencies, download the data and unpack it to the experiment folder;
- Upload Albert's files to ./content
- Run Join_ALBERT_LLM_Hivemind_quickstart.ipynb

Joining the Training

- Run:
  ```bash
  ./run_trainer.py  --initial_peers ONE_OR_MORE_PEERS --per_device_train_batch_size BATCH_SIZE_FOR_YOUR_GPU
  ```

  Here, `ONE_OR_MORE_PEERS` stands for multiaddresses of one or multiple existing peers (training monitors or existing
  trainers)
  collected from the first lines of their terminal output. For the example above, the multiaddresses would be:
  ```
  --initial_peers /dns4/x.tcp.ngrok.io/tcp/12345/p2p/ABCDE'
  ```

### Results

![W B Chart 2024_12_11 23_27_28](https://github.com/user-attachments/assets/251db64d-557c-4a9b-95b6-10c37334fc15)
![W B Chart 2024_12_11 23_27_38](https://github.com/user-attachments/assets/9845a678-7d9b-489b-9aa9-d7a7dd4e99a3)
![image](https://github.com/user-attachments/assets/8cc840ef-f15d-4fa9-b029-d4e19aee02d5)


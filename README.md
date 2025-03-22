# mem-follow: Cross-Chain Bridge Event Listener Simulation

This repository contains a Python script that simulates a critical component of a cross-chain bridge: an event listener node. This node's responsibility is to monitor a bridge contract on a source blockchain (e.g., Ethereum), detect deposit events, and trigger a corresponding token minting/release transaction on a destination blockchain (e.g., Polygon).

This script is designed as an architectural showcase, demonstrating principles of modular design, state management, resilience, and interaction with blockchain networks in a decentralized system.

## Concept

Cross-chain bridges are essential for blockchain interoperability, allowing assets and data to move between otherwise siloed networks. A common design pattern is the "lock-and-mint" mechanism:

1.  **Lock/Deposit:** A user deposits tokens (e.g., ERC-20) into a bridge contract on the source chain. The contract locks these tokens and emits an event (`TokensDeposited`) containing details like the recipient's address on the destination chain, the amount, and the destination chain ID.
2.  **Listen:** Off-chain nodes, often called validators or relayers, constantly monitor the source chain for these `TokensDeposited` events.
3.  **Verify & Relay:** Upon detecting a valid event, a relayer node constructs, signs, and broadcasts a new transaction on the destination chain. This transaction calls a function (e.g., `mintBridgedTokens`) on the destination bridge contract.
4.  **Mint/Release:** The destination bridge contract verifies the relayer's message and mints an equivalent amount of "wrapped" tokens to the specified recipient's address.

This script simulates the entire lifecycle of the **Listen** and **Verify & Relay** steps.

## Code Architecture

The script is structured into several distinct classes, each with a single responsibility, promoting maintainability and testability.

```
+---------------------------+
|          main.py          |
| (Setup & Orchestration)   |
+-------------+-------------+
              |
              | Starts
              v
+-------------+-----------------+
|   BridgeContractListener      |  (Thread)
|-------------------------------|
| - Manages event filter        |
| - Main listening loop         |
| - Handles re-orgs, errors     |
| - Coordinates other components|
+-------------+-----------------+
              |                   |                      |
      (1. Event) v                  (2. Process) v             (3. Broadcast) v
+--------------+----------+  +-----------------+  +----------------------+
|   ChainConnector        |  |  EventProcessor |  | TransactionBroadcaster |
| (Source & Destination)  |  |-----------------|  |----------------------|
|-------------------------|  | - Validates     |  | - Builds transaction |
| - Manages Web3 connection |  |   event data    |  | - Manages nonce      |
| - Provides contract obj |  | - Prevents      |  | - Signs & Sends      |
| - Retry logic           |  |   replays via   |  | - Waits for receipt  |
+-------------------------+  |   StateDB       |  | - Gas estimation     |
                           +--------+--------+  +----------------------+
                                    |
                               (Checks/Updates)
                                    v
                           +-----------------+
                           |     StateDB     |
                           |-----------------|
                           | - Persists state|
                           |   (JSON file)   |
                           +-----------------+
```

-   **`ChainConnector`**: Manages the connection to a specific blockchain's RPC endpoint. It handles initial connection, status checks, and provides Web3 contract objects.
-   **`StateDB`**: A simple persistent state manager. It keeps track of which event transaction hashes have already been processed to prevent duplicates (replay attacks). In this simulation, it uses a local JSON file.
-   **`EventProcessor`**: The business logic core. It takes a raw event, validates its arguments (e.g., checks if it's for the correct destination chain), and transforms it into a structured payload ready for the destination chain transaction.
-   **`TransactionBroadcaster`**: Responsible for the final step. It takes the processed data, builds the raw transaction (including nonce and gas), signs it with a private key, and broadcasts it to the destination chain. It includes a `SIMULATE_ONLY` mode.
-   **`BridgeContractListener`**: The main orchestrator, running in its own thread. It sets up an event filter on the source contract, polls for new events, and passes them through the `EventProcessor` and `TransactionBroadcaster` in a continuous loop. It also includes basic logic for handling block re-orgs and connection errors.

## How it Works

1.  **Initialization**: The `main` function reads configuration from a `.env` file, including RPC URLs, contract addresses, and a private key for the relayer wallet.
2.  **Connection**: It establishes connections to both the source and destination chains using `ChainConnector` instances.
3.  **State Loading**: `StateDB` loads the list of previously processed transaction hashes from `processed_events_db.json`.
4.  **Listener Start**: The `BridgeContractListener` is instantiated and started in a separate thread.
5.  **Polling Loop**: The listener thread enters a loop where it periodically polls the source chain's bridge contract for new `TokensDeposited` events using `web3.eth.filter`.
6.  **Confirmation Delay**: To protect against blockchain re-organizations, the script waits for a configurable number of blocks (`confirmation_blocks`) to pass before processing an event.
7.  **Event Processing**: For each confirmed event, the `EventProcessor` is called.
    -   It first checks the `StateDB` to ensure the event hasn't been processed before.
    -   It validates the event's data.
    -   If valid, it prepares a dictionary with the necessary data for the minting transaction.
8.  **Transaction Broadcasting**: The prepared data is passed to the `TransactionBroadcaster`.
    -   It fetches the current nonce for the relayer account.
    -   It builds and signs the `mintBridgedTokens` transaction.
    -   If `SIMULATE_ONLY` is `False`, it sends the transaction to the destination chain and waits for the receipt.
9.  **State Update**: Upon successful broadcast, the `StateDB` is updated with the source event's transaction hash, preventing it from being processed again.
10. **Graceful Shutdown**: The script listens for a `KeyboardInterrupt` (Ctrl+C) to shut down the listener thread cleanly.

## Usage Example

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/your-username/mem-follow.git
    cd mem-follow
    ```

2.  **Create a virtual environment and install dependencies:**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    pip install -r requirements.txt
    ```

3.  **Set up your environment variables:**
    Create a file named `.env` in the root directory and populate it with your own data. You can use services like [Infura](https://infura.io) or [Alchemy](https://www.alchemy.com/) to get RPC URLs.

    **Example `.env` file:**
    ```env
    # --- SOURCE CHAIN (e.g., Ethereum Goerli Testnet) ---
    SOURCE_CHAIN_RPC="https://goerli.infura.io/v3/YOUR_INFURA_PROJECT_ID"
    # A mock address; replace with a real bridge contract if you have one
    SOURCE_BRIDGE_CONTRACT="0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B"

    # --- DESTINATION CHAIN (e.g., Polygon Mumbai Testnet) ---
    DEST_CHAIN_RPC="https://polygon-mumbai.infura.io/v3/YOUR_INFURA_PROJECT_ID"
    DEST_CHAIN_ID=80001
    # A mock address; replace with a real mintable contract if you have one
    DEST_MINT_CONTRACT="0x4B29A516006a8A3951295a4deaB5155a6a25691C"

    # --- RELAYER WALLET ---
    # IMPORTANT: Use a dedicated burner wallet with only enough funds for gas fees.
    # DO NOT USE A WALLET WITH SIGNIFICANT ASSETS.
    SIGNER_PRIVATE_KEY="0xyour_private_key_here"

    # --- SCRIPT BEHAVIOR ---
    # Set to 'False' to actually broadcast transactions. Default is 'True'.
    SIMULATE_ONLY="True"
    ```

4.  **Run the script:**
    ```bash
    python script.py
    ```

5.  **Observe the output:**
    The script will start logging its status. If a `TokensDeposited` event occurs on the source contract that matches the criteria, you will see logs detailing the processing and (simulated) broadcasting steps.

    ```
    2023-10-27 15:30:00 - INFO - [MainThread] - Successfully connected to SourceChain at https://goerli.infura.io/v3/...
    2023-10-27 15:30:01 - INFO - [MainThread] - Successfully connected to DestinationChain at https://polygon-mumbai.infura.io/v3/...
    2023-10-27 15:30:01 - INFO - [BridgeListenerThread] - Starting to listen for 'TokensDeposited' events on 0xAb5801a7D398351b8bE11C439e05C5B3259aeC9B...
    ...
    2023-10-27 15:32:15 - INFO - [BridgeListenerThread] - Processing new event from tx: 0x123abc...def456
    2023-10-27 15:32:15 - INFO - [BridgeListenerThread] - Prepared transaction for destination chain: {'recipient': '0x...', 'amount': 100000000, ...}
    2023-10-27 15:32:15 - INFO - [BridgeListenerThread] - Broadcasting tx for source hash 0x123abc...def456...
    2023-10-27 15:32:16 - WARNING - [BridgeListenerThread] - [SIMULATION MODE] Would broadcast transaction. Hash: 0x789ghi...jkl123
    2023-10-27 15:32:16 - INFO - [BridgeListenerThread] - Marked transaction 0x123abc...def456 as processed.
    ```

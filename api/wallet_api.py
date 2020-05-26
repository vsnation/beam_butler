import json

import requests


class WalletAPI:

    def __init__(self, httpprovider):
        self.httpprovider = httpprovider

    """
        Create new wallet for new bot member
    """
    def create_user_wallet(self):
        response = requests.post(
            self.httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "create_address",
                    "params":
                        {
                            "expiration": "never"
                        }
                })).json()

        print(response)
        return response['result']

    """
        Fetch list of txs
    """
    def get_txs_list(self):
        response = requests.post(
            self.httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "tx_list",
                    "params":
                        {
                            "count": 100,
                        }
                })).json()

        return response

    """
        Send transaction
    """
    def send_transaction(
            self,
            value,
            fee,
            from_address,
            to_address,
            comment
    ):
        try:
            response = requests.post(
                self.httpprovider,
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tx_send",
                    "params":
                        {
                            "value": value,
                            "fee": fee,
                            "from": from_address,
                            "address": to_address,
                            "comment": comment
                        }
                })).json()
            print(response)
            return response
        except Exception as exc:
            print(exc)

    """
        Get wallet status
    """
    def get_wallet_status(self):
        try:
            response = requests.post(
                self.httpprovider,
                data=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 6,
                        "method": "wallet_status",
                    })).json()

            print(response)
            return response
        except Exception as exc:
            print(exc)

    """
        Get transaction status
    """
    def get_tx_status(self, tx_id):
        response = requests.post(
            self.httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tx_status",
                    "params":
                        {
                            "txId": "%s" % tx_id
                        }
                })).json()

        print(response)
        return response

    """
        Get utxo status
    """
    def get_utxo(self):
        response = requests.post(
            self.httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "get_utxo",
                    "params":
                        {
                            "count": 10,
                            "skip": 0
                        }
                })).json()
        print(response)
        return response

    """
        Cancel Transaction
    """
    def cancel_tx(self, tx_id):
        response = requests.post(
            self.httpprovider,
            data=json.dumps(
                {
                    "jsonrpc":"2.0",
                    "id": 4,
                    "method":"tx_cancel",
                    "params":
                    {
                        "txId" : tx_id
                    }
                }
            )).json()

        print(response)
        return response

    """
        Validate address
    """
    def validate_address(self, address):
        response = requests.post(
            self.httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "validate_address",
                    "params":
                        {
                            "address": "%s" % address
                        }
                })).json()
        return response

    """
        Split coins
    """
    def split_coins(self, coins):
        response = requests.post(
            self.httpprovider,
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tx_split",
                    "params":
                        {

                            "coins": coins,
                            "fee": 10000
                        }
                })).json()

        return response

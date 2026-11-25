"""Exact part-number matching and destination lookup."""
from dip_inspection.core.configuration import load_json, validate_products


class ProductManager:

    """Load the configured product catalog and resolve enabled destinations."""
    def __init__(
        self,
        config_path="products.json"
    ):
        self.config_path = config_path

        self.products = []
        self.reject_destination = 5

        self.load()

    def load(self):

        config, warning = load_json(self.config_path, validate_products)
        self.reject_destination = config["reject_destination"]
        self.products = config["products"]
        self.warning = warning

        print(
            f"[PRODUCTS] "
            f"{len(self.products)} "
            f"produtos carregados."
        )

    def classify(
        self,
        part_number
    ):

        if not part_number:
            return None

        pn = (
            part_number
            .strip()
            .upper()
        )

        for product in self.products:

            if not product.get(
                "enabled",
                True
            ):
                continue

            configured_pn = (
                product
                .get(
                    "part_number",
                    ""
                )
                .strip()
                .upper()
            )

            if pn == configured_pn:

                return {
                    "part_number":
                        configured_pn,

                    "destination":
                        int(
                            product[
                                "destination"
                            ]
                        )
                }

        return None

    def get_reject_destination(self):

        return (
            self.reject_destination
        )
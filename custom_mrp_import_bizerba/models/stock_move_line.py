# Copyright 2025 Berezi Amubieta - AvanzOSC
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html).
from datetime import datetime

import pytz

from odoo import _, api, models
from odoo.exceptions import ValidationError


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    @api.onchange("reader")
    def onchange_reader(self):
        if self.reader:
            if (
                len(self.reader) == 44
                and self.reader[16] == "1"
                and self.reader[17] == "5"
            ):
                self.reader_44_15(reader=self.reader)
            elif (
                len(self.reader) == 46
                and self.reader[16] == "3"
                and self.reader[17] == "1"
            ):
                self.reader_46_31(reader=self.reader)
            elif len(self.reader) == 13:
                self.reader_customerinfo(reader=self.reader)
            else:
                message = _("Unidentified barcode format : %(reader)s") % {
                    "reader": self.reader,
                }
                raise ValidationError(message)

    def reader_customerinfo(self, reader):
        self.ensure_one()
        if not self.picking_id and not self.picking_id.partner_id:
            message = _("The picking has no partner.")
            raise ValidationError(message)
        if reader:
            product_code = reader[0:7]
            product_code = product_code.lstrip("0")
        product_customerinfo = self.env["product.customerinfo"].search(
            [
                ("product_code", "=", product_code),
                "|",
                ("name", "=", self.picking_id.partner_id.id),
                ("name", "=", self.picking_id.partner_id.parent_id.id),
            ]
        )
        if not product_customerinfo:
            message = _(
                "No product found for product code %(code)s and partner %(partner)s"
            ) % {"code": product_code, "partner": self.picking_id.partner_id.name}
            raise ValidationError(message)
        if len(product_customerinfo) > 1:
            message = _(
                "More than one product found for product code %(code)s and partner %(partner)s"
            ) % {"code": product_code, "partner": self.picking_id.partner_id.name}
            raise ValidationError(message)
        if product_customerinfo:
            product = (
                product_customerinfo.product_id
                or product_customerinfo.product_tmpl_id.product_variant_id
            )
            if product and self.product_id and product != self.product_id:
                message = _(
                    "The product of the line %(product)s is not the "
                    + "same as the product found %(product_found)s"
                ) % {"product": self.product_id.name, "product_found": product.name}
                raise ValidationError(message)
            self.product_id = product.id
            if self.picking_id and self.picking_id.move_ids_without_package:
                self.move_id = (
                    self.picking_id.move_ids_without_package.filtered(
                        lambda c: c.product_id == product
                    )[:1]
                    or False
                )
            quantity = reader[7:10]
            quantity_decimal = reader[10:12]
            quantity = quantity + "." + quantity_decimal
            quantity = float(quantity)
            if product_customerinfo.final_partner_price:
                quantity = quantity / product_customerinfo.final_partner_price
            self.write({"quantity": quantity})

    def reader_44_15(self, reader):
        self.ensure_one()
        if reader and len(reader) == 44 and reader[16] == "1" and reader[17] == "5":
            product_code = reader[5:15]
            product_code = product_code.lstrip("0")
            product_domain = [("default_code", "=", product_code)]
            product = (
                self.env["product.product"]
                .search(product_domain)
                .filtered(
                    lambda c: c.company_id == self.company_id
                    or self.company_id in c.company_ids
                )
            )
            if not product:
                product_domain = [("bizerba_code", "=", product_code.zfill(3))]
                product = (
                    self.env["product.product"]
                    .search(product_domain)
                    .filtered(
                        lambda c: c.company_id == self.company_id
                        or self.company_id in c.company_ids
                    )
                )
                if not product:
                    message = _(
                        "Product not found, reader information for product code: %(reader)s"
                    ) % {
                        "reader": product_code,
                    }
                    raise ValidationError(message)
            if self.product_id and self.product_id != product:
                message = _(
                    "The product of the reader, %(reader)s, is not the same of the line."
                ) % {
                    "reader": product.display_name,
                }
                raise ValidationError(message)
            if len(product) > 1:
                message = _("More than one product found with code: %(reader)s.") % {
                    "reader": product_code,
                }
                raise ValidationError(message)
            if product:
                self.check_product_in_bom(
                    product=product, production=self.production_id
                )
            self.product_id = product.id
            if self.picking_id and self.picking_id.move_ids_without_package:
                self.move_id = (
                    self.picking_id.move_ids_without_package.filtered(
                        lambda c: c.product_id == product
                    )[:1]
                    or False
                )
            expiration_date = reader[18:24]
            timezone = pytz.timezone(self._context.get("tz") or "UTC")
            expiration_date = datetime.strptime(expiration_date, "%y%m%d")
            expiration_date = timezone.localize(expiration_date).astimezone(pytz.UTC)
            expiration_date = expiration_date.replace(tzinfo=None)
            if int(reader[27]) <= 6:
                k = 6 - int(reader[27])
                quantity = reader[28 : 28 + k]
                quantity_decimal = reader[28 + k : 34]
            else:
                quantity = reader[28:31]
                quantity_decimal = reader[31:34]
            quantity = quantity + "." + quantity_decimal
            quantity = float(quantity)
            lot_name = reader[36:44]
            lot_domain = [
                ("name", "=", lot_name),
                ("company_id", "=", self.company_id.id),
                ("product_id", "=", product.id),
            ]
            lot = self.env["stock.lot"].search(lot_domain)
            if not lot:
                lot = self.env["stock.lot"].action_create_lot(
                    product, lot_name, self.company_id
                )
            self.write(
                {
                    "quantity": quantity,
                    "lot_id": lot.id,
                }
            )
            self.write(
                {
                    "expiration_date": expiration_date,
                }
            )

    def reader_46_31(self, reader):
        self.ensure_one()
        if reader and len(reader) == 46 and reader[16] == "3" and reader[17] == "1":
            product_code = reader[5:15]
            product_code = product_code.lstrip("0")
            product_domain = [("default_code", "=", product_code)]
            product = (
                self.env["product.product"]
                .search(product_domain)
                .filtered(
                    lambda c: c.company_id == self.company_id
                    or self.company_id in c.company_ids
                )
            )
            if not product:
                product_domain = [("bizerba_code", "=", product_code.zfill(3))]
                product = (
                    self.env["product.product"]
                    .search(product_domain)
                    .filtered(
                        lambda c: c.company_id == self.company_id
                        or self.company_id in c.company_ids
                    )
                )
                if not product:
                    message = _(
                        "Product not found, reader information for product code: %(reader)s"
                    ) % {
                        "reader": product_code,
                    }
                    raise ValidationError(message)
            if len(product) > 1:
                message = _("More than one product found with code: %(reader)s.") % {
                    "reader": product_code,
                }
                raise ValidationError(message)
            if self.product_id and self.product_id != product:
                message = _(
                    "The product of the reader, %(reader)s, is not the same of the line."
                ) % {
                    "reader": product.display_name,
                }
                raise ValidationError(message)
            if product:
                self.check_product_in_bom(
                    product=product, production=self.production_id
                )
            self.product_id = product.id
            if self.picking_id and self.picking_id.move_ids_without_package:
                self.move_id = (
                    self.picking_id.move_ids_without_package.filtered(
                        lambda c: c.product_id == product
                    )[:1]
                    or False
                )
            if int(reader[19]) <= 6:
                k = 6 - int(reader[19])
                quantity = reader[20 : 20 + k]
                quantity_decimal = reader[20 + k : 26]
            else:
                quantity = reader[20:23]
                quantity_decimal = reader[23:26]
            quantity = quantity + "." + quantity_decimal
            container = int(reader[28:36])
            lot_name = reader[38:46]
            lot_domain = [
                ("name", "=", lot_name),
                ("company_id", "=", self.company_id.id),
                ("product_id", "=", product.id),
            ]
            lot = self.env["stock.lot"].search(lot_domain)
            if not lot:
                lot = self.env["stock.lot"].action_create_lot(
                    product, lot_name, self.company_id
                )
            vals = {"container": container, "quantity": quantity, "lot_id": lot.id}
            self.write(vals)

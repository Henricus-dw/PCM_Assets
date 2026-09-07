-- Matches TabletFormEntry in PCM_Tracer/models.py
CREATE TABLE IF NOT EXISTS `TabletForm` (
    `id` INT NOT NULL AUTO_INCREMENT,
    `entry_date` DATE NOT NULL,
    `entry_time` TIME NOT NULL,
    `bin_location` VARCHAR(100) NOT NULL,
    `sku_barcode` VARCHAR(100) NOT NULL,
    `qty` INT NOT NULL,
    `picker_detail` VARCHAR(150) NOT NULL,
    `shipment_number` VARCHAR(100) NOT NULL,
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

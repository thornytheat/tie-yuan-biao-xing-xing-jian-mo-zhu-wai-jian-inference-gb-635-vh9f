-- 禾丰零售 · 贴源层 DDL 导出（采购的数仓建模工具生成，未人工整理）
-- 2026-11-13 导出；odl_ 前缀 = 贴源表，字段与来源系统同名
-- 已知问题：store 表有两个团队各建一次的列（大小写不同）；payment 表未声明外键

CREATE TABLE odl_region (
  region_code   varchar(2)    NOT NULL COMMENT '区域编码',
  region_name   varchar(20)   NOT NULL COMMENT '区域名称',
  manager       varchar(20)            COMMENT '负责人',
  updated_at    timestamp              COMMENT '更新时间',
  PRIMARY KEY (region_code)
);

CREATE TABLE odl_store (
  store_code    varchar(4)    NOT NULL COMMENT '门店编码',
  Store_Code    varchar(4)             COMMENT '门店编码（旧列，未清理）',
  store_name    varchar(30)   NOT NULL COMMENT '门店名称',
  region_code   varchar(2)             COMMENT '区域编码',
  open_date     date                   COMMENT '开业日期',
  store_level   varchar(4)             COMMENT '门店等级',
  updated_at    timestamp              COMMENT '更新时间',
  PRIMARY KEY (store_code),
  FOREIGN KEY (region_code) REFERENCES odl_region(region_code)
);

CREATE TABLE odl_product (
  product_code  varchar(5)    NOT NULL COMMENT '商品编码',
  product_name  varchar(40)   NOT NULL COMMENT '商品名称',
  category_code varchar(4)             COMMENT '品类编码',
  brand         varchar(20)            COMMENT '品牌',
  list_price    numeric(10,2)          COMMENT '标价',
  PRIMARY KEY (product_code)
);

CREATE TABLE odl_member (
  member_id     varchar(4)    NOT NULL COMMENT '会员号',
  member_name   varchar(20)            COMMENT '会员姓名',
  phone         varchar(20)            COMMENT '手机号',
  join_date     varchar(10)            COMMENT '入会日期（v1 存文本，v2 改 date）',
  tier          varchar(10)            COMMENT '等级',
  points        int                    COMMENT '积分',
  PRIMARY KEY (member_id)
);

CREATE TABLE odl_sale_order (
  order_id      varchar(16)   NOT NULL COMMENT '订单号',
  store_code    varchar(4)    NOT NULL COMMENT '门店编码',
  member_id     varchar(4)             COMMENT '会员号',
  order_time    timestamp     NOT NULL COMMENT '下单时间',
  channel       varchar(10)            COMMENT '渠道',
  order_amount  numeric(12,2)          COMMENT '订单金额',
  status        varchar(6)             COMMENT '订单状态',
  coupon_code   varchar(12)            COMMENT '券码（本期未导出）',
  PRIMARY KEY (order_id),
  FOREIGN KEY (store_code) REFERENCES odl_store(store_code),
  FOREIGN KEY (member_id) REFERENCES odl_member(member_id)
);

CREATE TABLE odl_sale_order_item (
  order_id        varchar(20)  NOT NULL COMMENT '订单号（宽度比父表宽）',
  line_no         int          NOT NULL COMMENT '行号',
  product_code    varchar(5)   NOT NULL COMMENT '商品编码',
  qty             int                   COMMENT '数量',
  net_amount      numeric(12,2)         COMMENT '行净额',
  discount_amount numeric(12,2)         COMMENT '行折扣',
  PRIMARY KEY (order_id, line_no),
  FOREIGN KEY (order_id) REFERENCES odl_sale_order(order_id),
  FOREIGN KEY (product_code) REFERENCES odl_product(product_code)
);

CREATE TABLE odl_payment (
  id            bigint        NOT NULL COMMENT '支付流水号（19 位）',
  order_id      varchar(16)   NOT NULL COMMENT '订单号',
  pay_method    varchar(10)            COMMENT '支付方式',
  pay_amount    numeric(12,2)          COMMENT '支付金额',
  pay_time      timestamp              COMMENT '支付时间',
  PRIMARY KEY (id)
);

CREATE TABLE odl_inventory_snapshot (
  snapshot_date date          NOT NULL COMMENT '快照日期',
  store_code    varchar(4)    NOT NULL COMMENT '门店编码',
  product_code  varchar(5)    NOT NULL COMMENT '商品编码',
  on_hand_qty   int                    COMMENT '在手数量',
  updated_at    timestamp              COMMENT '更新时间',
  batch_no      varchar(8)             COMMENT '批次号（本期未导出）',
  PRIMARY KEY (snapshot_date, store_code, product_code),
  FOREIGN KEY (store_code) REFERENCES odl_store(store_code),
  FOREIGN KEY (product_code) REFERENCES odl_product(product_code)
);

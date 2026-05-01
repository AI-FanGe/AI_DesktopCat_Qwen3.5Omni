// camera_pins.h - XIAO ESP32S3 Sense Camera Pin Definitions

#ifndef CAMERA_PINS_H
#define CAMERA_PINS_H

#if defined(CAMERA_MODEL_XIAO_ESP32S3)

#define PWDN_GPIO_NUM     -1
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM     10
#define SIOD_GPIO_NUM     40
#define SIOC_GPIO_NUM     39

#define Y9_GPIO_NUM       48
#define Y8_GPIO_NUM       11
#define Y7_GPIO_NUM       12
#define Y6_GPIO_NUM       14
#define Y5_GPIO_NUM       16
#define Y4_GPIO_NUM       18
#define Y3_GPIO_NUM       17
#define Y2_GPIO_NUM       15
#define VSYNC_GPIO_NUM    38
#define HREF_GPIO_NUM     47
#define PCLK_GPIO_NUM     13

#else
#error "Camera model not selected"
#endif

#endif // CAMERA_PINS_H
















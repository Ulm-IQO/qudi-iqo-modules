#include <stdio.h>
#include <windows.h>

short *ai_buff1_address;
short *ai_buff2_address;
unsigned long buffer_size;

long long *qudi_buffer_address;
unsigned long number_of_measurements;
short buffer_id;

int test_callback() {
  printf("In callback \n");
  return 0;
}

void sum_buffer(short *buffer_address) {
  // Function that sums adlink buffer into measurement buffer, given the adlink
  // buffer address
  for (int i = 0; i < number_of_measurements; i++) {
    for (int j = 0; j < buffer_size; j++) {
      qudi_buffer_address[j] += (long long)buffer_address[i * buffer_size + j];
    }
  }
}

void sum_buffer_callback() {
  // Function that adds the adlink measurement buffers to a user-defined
  // measurement buffer. This measurement buffer only stores data of one
  // measurement and freshly acquired data is added on top.
  switch (buffer_id) {
  case 0:
    sum_buffer(ai_buff1_address);
    buffer_id = 1;
    break;
  case 1:
    sum_buffer(ai_buff2_address);
    buffer_id = 0;
    break;
  }
}

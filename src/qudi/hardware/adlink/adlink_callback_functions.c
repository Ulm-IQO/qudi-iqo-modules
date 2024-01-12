#include <stdio.h>
#include <string.h>
#include <time.h>
#include <windows.h>

short *ai_buff1_address;
short *ai_buff2_address;
unsigned long buffer_size;

short buffer_element_size;

short *total_buffer_address;
unsigned long total_buffer_length;
unsigned long current_buffer_position = 0;
unsigned long current_writer_position = 0;
short buffer_id;

FILE *file_pointer;
char *save_location;
DWORD file_writer_wait_time; // ms
unsigned int file_writer_stop = 0;
HANDLE thread_handle;
DWORD thread_id;

int (*python_restart_function_ptr)();

void set_restart_function_pointer(int (*function_ptr)()) {
  // sets the pointer to the restart function
  python_restart_function_ptr = function_ptr;
}

void error_printer(int buffer_id) {
  printf("buffer_id %d not known, available ids: 1, 2\n", buffer_id);
}

short *return_buffer(int buffer_id) {
  // returns buffer address given a buffer_id
  if (buffer_id == 0) {
    return ai_buff1_address;
  }
  if (buffer_id == 1) {
    return ai_buff2_address;
  }
  if (buffer_id == 2) {
    return total_buffer_address;
  } else {
    short *return_array;
    return return_array;
  }
}

int copy_double_buffer_callback() {
  // Function that copies the adlink measurement buffers to a larger
  // user-defined buffer, which stores the whole measurement
  switch (buffer_id) {
  case 0:
    memcpy((total_buffer_address + current_buffer_position), ai_buff1_address,
           buffer_size * sizeof(*ai_buff1_address));
    current_buffer_position += buffer_size;
    buffer_id = 1;
    break;
  case 1:
    memcpy((total_buffer_address + current_buffer_position), ai_buff2_address,
           buffer_size * sizeof(*ai_buff2_address));
    current_buffer_position += buffer_size;
    buffer_id = 0;
    break;
  }
  if (current_buffer_position >= total_buffer_length * buffer_size) {
    current_buffer_position = 0;
  }
  return 0;
}

int copy_double_buffer_callback_python_restart() {
  // Function that calls copy_double_buffer_callback and then calls the
  // function specified by python_restart_function_ptr
  int return_value = copy_double_buffer_callback();
  python_restart_function_ptr();
  return return_value;
}

int copy_double_buffer_callback_time_measured() {
  // Function that calls copy_double_buffer_callback and measures the time it
  // took
  clock_t begin = clock();
  int return_value = copy_double_buffer_callback();
  clock_t end = clock();
  double time_spent = (double)(end - begin) / CLOCKS_PER_SEC;
  printf("time needed for copying buffer: %f\n", time_spent);
  return 0;
}

int return_buffer_value(int buffer_id) {
  // Function that returns the first entry of the specified buffer
  if (buffer_id == 0) {
    return *ai_buff1_address;
  }
  if (buffer_id == 1) {
    return *ai_buff2_address;
  }
  if (buffer_id == 2) {
    return *total_buffer_address;
  } else {
    error_printer(buffer_id);
    return -1;
  }
}

int create_file_writer() {
  // Function that opens the file stream and assigns the file_pointer handle
  int err = fopen_s(&file_pointer, save_location, "ab");
  buffer_element_size = sizeof(total_buffer_address[0]);
  return err;
}

int write_to_file() {
  // Function that uses file_pointer handle to write from large, user-defined
  // buffer to file. Only writes newly acquired data to file
  unsigned long number_of_elements =
      current_buffer_position - current_writer_position;
  int err = fwrite(&total_buffer_address[current_writer_position],
                   buffer_element_size, number_of_elements, file_pointer);
  current_writer_position += number_of_elements;
  return err;
}

int close_file_writer() {
  // Closes file stream and releases it upon completion
  int err = fclose(file_pointer);
  return err;
}

DWORD WINAPI file_writer() {
  // Thread function that constantly checks for changes in the buffer and
  // writes them to the binary file file_writer_wait_time is the time in ms
  // between consecutive checks of buffer changes if a manual stop is
  // required, set file_writer_stop flag to 1
  printf("Starting file writer thread!\n");
  while (!file_writer_stop) {
    if (current_writer_position >= current_buffer_position) {
      Sleep(file_writer_wait_time);
      continue;
    }
    write_to_file();
    Sleep(file_writer_wait_time);
  }
  return 0;
}

int create_thread() {
  // Function that creates the thread for constant, simultaneous buffer change
  // monitoring and saving those changes to a binary file
  printf("Creating file writer thread!\n");
  file_writer_stop = 0;
  create_file_writer();
  thread_handle = CreateThread(NULL, 0, (LPTHREAD_START_ROUTINE)file_writer,
                               NULL, 0, &thread_id);
  return 0;
}

int close_thread() {
  // Function to close the thread and release the file stream
  printf("Closing file writer thread!\n");
  file_writer_stop = 1;
  WaitForSingleObject(thread_handle, INFINITE);
  CloseHandle(thread_handle);
  close_file_writer();
  printf("File writer thread closed!\n");
  return 0;
}

class DataProcessorUngated:
    def __init__(self, data, fetcher):
        self.dc_new = data.dc_new
        self.dc = data.dc
        self.avg = data.avg
        self.avg_new = data.avg_new
        self.fetcher = fetcher

    def process_data(self, curr_avail_reps, user_pos_B, *args):
        self._fetch_data(curr_avail_reps, user_pos_B)
        self._get_new_avg_data()

    def get_initial_avg(self):
        self.avg.data = self.avg_new.data
        self.avg.num = self.avg_new.num

    def update_data(self, curr_avail_reps, user_pos_B, *args):
        self.process_data(curr_avail_reps, user_pos_B)
        self._update_avg_data()

    def _fetch_data(self, curr_avail_reps, user_pos_B):
        self.dc_new.data = self.fetcher.fetch_data(curr_avail_reps, user_pos_B)
        self.dc_new.rep = curr_avail_reps
        self.dc_new.set_len()
        self.dc_new.data = self.dc_new.reshape_2d_by_rep()

    def stack_new_data(self):
        self.dc.stack_rep(self.dc_new)

    def _get_new_avg_data(self):
        self.avg_new.data = self.dc_new.avgdata()
        self.avg_new.num = self.dc_new.rep

    def _update_avg_data(self):
        self.avg.update(self.avg_new)
        self.avg.set_len()

class DataProcessGated(DataProcessorUngated):

    def process_data(self, curr_avail_reps, user_pos_B, ts_user_pos_B):
        self._fetch_data(curr_avail_reps, user_pos_B)
        self._fetch_ts(curr_avail_reps, ts_user_pos_B)
        self._get_new_avg_data()

    def update_data(self, curr_avail_reps, user_pos_B, ts_user_pos_B):
        self.process_data(curr_avail_reps, user_pos_B, ts_user_pos_B)
        self._update_avg_data()

    def _fetch_ts(self, curr_avail_reps, ts_user_pos_B):
        self.dc_new.ts_r, self.dc_new.ts_f = self.fetcher.fetch_ts_data(curr_avail_reps, ts_user_pos_B)

    def stack_new_ts(self):
        self.dc.stack_rep_gated(self.dc_new)






